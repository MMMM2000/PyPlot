"""PyQt6 user interface for the microwire database builder."""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import time
import os
from datetime import datetime
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd

from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
import matplotlib.pyplot as plt

from plotting.current_annealing.core import plot_one as plot_annealing_curve
from plotting.pyplot import _DockSwitcherWidget
from plotting.utils import (
    ensure_app_theme,
    install_standard_menu,
    format_annealing_title,
    developer_options,
)
from origin_clone.app import PythonConsoleWidget

from .storage import MiniDatabaseData, MiniDatabaseStore

from .core import (
    LOGGER_NAME,
    DEFAULT_FIGSIZE,
    DEFAULT_OUTPUT_NAME,
    BuildResult,
    BuilderConfig,
    BuildCancelledError,
    MicroscopeMeasurements,
    MicroscopeDetection,
    MicroscopeOCRResult,
    MeasurementRecord,
    VideoMetricsSummary,
    FabricationIndex,
    StrainRecord,
    build_database,
    build_fabrication_index,
    _normalise_output_name,
    _metadata_from_path,
    _microscope_key,
    _draw_key,
    _load_annealing,
    _resistance_sanity_check,
    _group_microscope_measurements,
    _collect_video_metrics,
    _microwire_label,
    _microwire_tuple_from_label,
    _parse_strain_float,
    _plot_measurement_matplotlib,
    _select_high_measurement,
    _select_low_measurement,
    _value_for_output,
    _compose_notes,
    _format_dimension_display,
    _clean_str,
)


MICROSCOPE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov")


ANNEALING_GRAPH_WIDTH = 420
ANNEALING_GRAPH_HEIGHT = 200
ANNEALING_TITLE_FONT_SIZE = 8
ANNEALING_AXIS_FONT_SIZE = 6
ANNEALING_TICK_FONT_SIZE = 6


_STAGE_LABELS = {
    "prep": "Preparing support files",
    "analysis": "Analysing microscope/video data",
    "build": "Building database rows",
    "final": "Finalising exports",
}


@dataclass
class WorkerInputs:
    """Configuration passed to :class:`BuildWorker`."""

    annealing_files: list[Path]
    manual_microscope_files: list[Path]
    data_roots: list[Path]
    output_dir: Path
    output_name: str
    export_formats: tuple[str, ...]
    plot_backends: tuple[str, ...]
    export_behaviour: dict[str, str]
    matplotlib_figsize: tuple[float, float]
    include_microscope_crops: bool = True
    highlight_ocr_values: bool = True
    analyse_videos: bool = True
    strain_files: list[Path] = field(default_factory=list)


FIGURE_WIDTH_DEFAULT_MM = round(DEFAULT_FIGSIZE[0] * 25.4, 1)
FIGURE_HEIGHT_DEFAULT_MM = round(DEFAULT_FIGSIZE[1] * 25.4, 1)


def is_microscope_candidate(path: Path) -> bool:
    """Return ``True`` when ``path`` looks like a microscope overlay image."""

    if path.suffix.lower() not in MICROSCOPE_EXTENSIONS:
        return False
    stem = path.stem.lower()
    return "core" in stem or "glass" in stem


def collect_support_files(
    annealing_files: Sequence[Path],
    data_roots: Sequence[Path],
    progress_callback: Optional[Callable[[], None]] = None,
    include_videos: bool = True,
) -> tuple[list[Path], list[Path], list[Path]]:
    """Locate fabrication spreadsheets, microscope images and videos.

    When ``progress_callback`` is supplied it is invoked after each annealing
    file has been analysed so callers can surface responsive progress updates
    while the filesystem scan is running. Set ``include_videos`` to ``False`` to
    skip the slower video discovery path entirely.
    """

    if not annealing_files:
        return [], [], []

    unique_annealing = list(dict.fromkeys(Path(p) for p in annealing_files))
    records: list[tuple[Path, object]] = []
    for path in unique_annealing:
        try:
            meta = _metadata_from_path(path)
        except Exception:
            continue
        composition = getattr(meta, "composition_token", None)
        draw = getattr(meta, "draw_x", None)
        if composition and draw is not None:
            records.append((path, meta))
    if not records:
        return [], [], []

    def _is_relative(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    candidate_roots: list[Path] = []
    for candidate in dict.fromkeys(data_roots):
        root_path = Path(candidate).expanduser()
        if root_path.is_dir():
            candidate_roots.append(root_path)
    primary_root: Path | None = None
    for root_path in candidate_roots:
        if any(_is_relative(path, root_path) for path, _ in records):
            primary_root = root_path
            break
    if primary_root is None:
        if candidate_roots:
            primary_root = candidate_roots[0]
        else:
            common = Path(records[0][0]).resolve().parent
            primary_root = common

    def _resolve_subdir(root: Path | None, names: tuple[str, ...]) -> Path | None:
        if root is None:
            return None
        for name in names:
            candidate = root / name
            if candidate.is_dir():
                return candidate
        return None

    fabrication_root = (
        _resolve_subdir(primary_root, ("microwire data", "Microwire data"))
        or primary_root
    )
    microscope_root = _resolve_subdir(primary_root, ("microscope", "Microscope"))
    video_root = _resolve_subdir(
        primary_root,
        ("microwire data", "Microwire data", "videos", "Videos", "Microscope", "microscope"),
    )

    fabrication: list[Path] = []
    auto_micro: list[Path] = []
    videos: list[Path] = []
    seen_fabrication: set[Path] = set()
    seen_micro: set[Path] = set()
    seen_video: set[Path] = set()

    def _append_unique(container: list[Path], seen: set[Path], candidate: Path | None) -> None:
        if candidate is None:
            return
        resolved = candidate.expanduser()
        try:
            exists = resolved.exists()
        except OSError:
            exists = False
        if not exists:
            return
        try:
            key = resolved.resolve()
        except OSError:
            key = resolved
        if key in seen:
            return
        seen.add(key)
        container.append(resolved)

    def _composition_dirs(base: Path | None, composition: str) -> list[Path]:
        dirs: list[Path] = []
        if base is None or not base.is_dir():
            return dirs
        exact = base / composition
        if exact.is_dir():
            dirs.append(exact)
        try:
            for child in base.iterdir():
                if child.is_dir() and child.name.lower().startswith(composition.lower()):
                    dirs.append(child)
        except OSError:
            pass
        if not dirs:
            dirs.append(base)
        return dirs

    def _piece_dirs(comp_dir: Path, draw: int) -> list[Path]:
        dirs: list[Path] = []
        draw_token = str(draw)
        try:
            for child in comp_dir.iterdir():
                if child.is_dir() and draw_token in child.name:
                    dirs.append(child)
        except OSError:
            pass
        return dirs

    def _iter_fragment_files(
        root: Path,
        fragment: str,
        extensions: tuple[str, ...],
        *,
        max_depth: int = 4,
        limit: int | None = None,
    ) -> list[Path]:
        if root is None or not root.exists():
            return []
        fragment_lower = fragment.lower()
        stack: list[tuple[Path, int]] = [(root, 0)]
        visited: set[Path] = set()
        matches: list[Path] = []
        keywords = ("microscope", "video", "videos")
        while stack:
            current_root, depth = stack.pop()
            if current_root in visited:
                continue
            visited.add(current_root)
            if depth > max_depth:
                continue
            try:
                entries = list(current_root.iterdir())
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir():
                        if entry.name.lower() in keywords:
                            next_depth = depth
                        else:
                            next_depth = depth + 1
                        stack.append((entry, next_depth))
                    elif entry.is_file():
                        if (
                            entry.suffix.lower() in extensions
                            and fragment_lower in entry.name.lower()
                        ):
                            matches.append(entry)
                            if limit is not None and len(matches) >= limit:
                                return matches
                except OSError:
                    continue
        return matches

    for _, meta in records:
        try:
            composition = getattr(meta, "composition_token", None)
            draw = getattr(meta, "draw_x", None)
            piece = getattr(meta, "piece_y", None)
            if composition is None or draw is None:
                continue
            composition_dirs = _composition_dirs(fabrication_root, composition)
            for comp_dir in composition_dirs:
                _append_unique(fabrication, seen_fabrication, comp_dir / f"{composition}.xlsx")
                try:
                    for candidate in comp_dir.glob("*.xlsx"):
                        if candidate.name.lower() == f"{composition.lower()}.xlsx":
                            continue
                        if candidate.stem.startswith(f"{draw}_"):
                            _append_unique(fabrication, seen_fabrication, candidate)
                except OSError:
                    pass
                if piece is not None:
                    for piece_dir in _piece_dirs(comp_dir, draw):
                        try:
                            for candidate in piece_dir.glob("*.xlsx"):
                                _append_unique(fabrication, seen_fabrication, candidate)
                        except OSError:
                            continue
            if piece is None:
                continue
            search_dirs: list[Path] = []
            if microscope_root is not None:
                search_dirs.extend(_composition_dirs(microscope_root, composition))
            if not search_dirs and microscope_root is not None:
                search_dirs.append(microscope_root)
            for comp_dir in composition_dirs:
                for piece_dir in _piece_dirs(comp_dir, draw):
                    if piece_dir not in search_dirs:
                        search_dirs.append(piece_dir)
            fragment_tokens = {
                f"{draw}_{piece}",
                f"{draw}-{piece}",
                f"{draw}{piece}",
                f"{draw} {piece}",
                f"{draw}.{piece}",
            }
            for search_dir in search_dirs:
                if not search_dir.is_dir():
                    continue
                try:
                    candidates: list[Path] = []
                    for fragment in fragment_tokens:
                        candidates.extend(
                            _iter_fragment_files(
                                search_dir,
                                fragment,
                                MICROSCOPE_EXTENSIONS,
                                limit=50,
                            )
                        )
                except Exception:
                    continue
                if not candidates:
                    try:
                        candidates = _iter_fragment_files(
                            search_dir, "", MICROSCOPE_EXTENSIONS, limit=100
                        )
                    except Exception:
                        continue
                for candidate in dict.fromkeys(candidates):
                    if not is_microscope_candidate(candidate):
                        continue
                    key = _microscope_key(candidate)
                    if key == (composition, draw, piece):
                        _append_unique(auto_micro, seen_micro, candidate)
            if include_videos:
                video_dirs: list[Path] = []
                if video_root is not None:
                    video_dirs.extend(_composition_dirs(video_root, composition))
                for comp_dir in composition_dirs:
                    video_dirs.extend(_piece_dirs(comp_dir, draw))
                for search_dir in video_dirs:
                    if not search_dir.is_dir():
                        continue
                    try:
                        video_candidates: list[Path] = []
                        for fragment in fragment_tokens:
                            video_candidates.extend(
                                _iter_fragment_files(
                                    search_dir, fragment, VIDEO_EXTENSIONS, limit=60
                                )
                            )
                    except Exception:
                        continue
                    if not video_candidates:
                        try:
                            video_candidates = _iter_fragment_files(
                                search_dir, "", VIDEO_EXTENSIONS, limit=120
                            )
                        except Exception:
                            continue
                    for candidate in dict.fromkeys(video_candidates):
                        key = _microscope_key(candidate)
                        if key == (composition, draw, piece):
                            _append_unique(videos, seen_video, candidate)
                            continue
                        draw_key = _draw_key(candidate)
                        if draw_key == (composition, draw):
                            _append_unique(videos, seen_video, candidate)
        finally:
            if progress_callback is not None:
                try:
                    progress_callback()
                except BuildCancelledError:
                    raise
                except Exception:
                    pass

    fabrication = list(dict.fromkeys(fabrication))
    auto_micro = list(dict.fromkeys(auto_micro))
    videos = list(dict.fromkeys(videos))
    return fabrication, auto_micro, videos


def _format_duration(seconds: float) -> str:
    if not math.isfinite(seconds):
        return "--"
    total_seconds = max(int(round(seconds)), 0)
    minutes, sec = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {sec:02d}s"
    if minutes:
        return f"{minutes:d}m {sec:02d}s"
    return f"{sec:d}s"


class QtLogHandler(logging.Handler):
    """Logging handler that forwards records to a Qt slot."""

    def __init__(self, emit: Callable[[int, str], None]) -> None:
        super().__init__()
        self._emit = emit

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - thin wrapper
        message = self.format(record)
        try:
            self._emit(record.levelno, message)
        except TypeError:
            # Fallback for legacy callbacks that only accept a message.
            self._emit(message)  # type: ignore[misc]


class _ProgressTracker:
    """Translate multi-phase progress updates into a single counter."""

    _ORDER = ("prep", "analysis", "build", "final")

    def __init__(
        self, emit: Callable[[str, int, int, int, int], None]
    ) -> None:
        self._emit_fn = emit
        self._totals = {stage: 0 for stage in self._ORDER}
        self._progress = {stage: 0 for stage in self._ORDER}

    def configure(
        self, prep_units: int, analysis_units: int, build_units: int, final_units: int = 1
    ) -> None:
        self._totals = {
            "prep": max(prep_units, 0),
            "analysis": max(analysis_units, 0),
            "build": max(build_units, 0),
            "final": max(final_units, 0),
        }
        self._progress = {key: 0 for key in self._totals}
        self._emit_all()

    def _total_units(self) -> int:
        total = sum(self._totals.values())
        return total if total > 0 else 1

    def _completed_units(self) -> int:
        total_units = self._total_units()
        completed = 0
        for key, total in self._totals.items():
            progress = self._progress.get(key, 0)
            if total <= 0:
                continue
            completed += min(max(progress, 0), total)
        return min(max(completed, 0), total_units)

    def _emit(self, stage: str) -> None:
        self._emit_fn(
            stage,
            self._progress.get(stage, 0),
            self._totals.get(stage, 0),
            self._completed_units(),
            self._total_units(),
        )

    def _emit_all(self) -> None:
        for stage in self._ORDER:
            self._emit(stage)

    def _set_total(self, key: str, units: int) -> None:
        units = max(units, 0)
        if units == self._totals.get(key, 0):
            return
        self._totals[key] = units
        if self._progress.get(key, 0) > units:
            self._progress[key] = units
        self._emit(key)

    def set_analysis_units(self, units: int) -> None:
        self._set_total("analysis", units)

    def set_final_units(self, units: int) -> None:
        self._set_total("final", units)

    def advance_prepare(self) -> None:
        total = self._totals.get("prep", 0)
        if total <= 0:
            return
        current = self._progress.get("prep", 0)
        if current < total:
            self._progress["prep"] = current + 1
            self._emit("prep")

    def finish_prepare(self) -> None:
        total = self._totals.get("prep", 0)
        if total <= 0:
            return
        if self._progress.get("prep", 0) < total:
            self._progress["prep"] = total
            self._emit("prep")

    def analysis_progress(self, current: int, total: int) -> None:
        total = max(total, 0)
        if total:
            self._set_total("analysis", total)
        mapped_total = self._totals.get("analysis", 0)
        if mapped_total <= 0:
            return
        clamped = min(max(current, 0), mapped_total)
        if clamped > self._progress.get("analysis", 0):
            self._progress["analysis"] = clamped
            self._emit("analysis")

    def finish_analysis(self) -> None:
        total = self._totals.get("analysis", 0)
        if total <= 0:
            return
        if self._progress.get("analysis", 0) < total:
            self._progress["analysis"] = total
            self._emit("analysis")

    def build_progress(self, current: int, total: int) -> None:
        total = max(total, 0)
        if total:
            self._set_total("build", total)
        mapped_total = self._totals.get("build", 0)
        if mapped_total <= 0:
            return
        clamped = min(max(current, 0), mapped_total)
        if clamped > self._progress.get("build", 0):
            self._progress["build"] = clamped
            self._emit("build")

    def finish_build(self) -> None:
        total = self._totals.get("build", 0)
        if total <= 0:
            return
        if self._progress.get("build", 0) < total:
            self._progress["build"] = total
            self._emit("build")

    def advance_final(self, units: int = 1) -> None:
        total = self._totals.get("final", 0)
        if total <= 0:
            return
        current = self._progress.get("final", 0)
        if current < total:
            self._progress["final"] = min(total, current + max(units, 1))
            self._emit("final")

    def finish_final(self) -> None:
        total = self._totals.get("final", 0)
        if total <= 0:
            return
        if self._progress.get("final", 0) < total:
            self._progress["final"] = total
            self._emit("final")

    def finalize(self) -> None:
        self.finish_final()


class BuildWorker(QtCore.QObject):
    """Background worker that runs the database builder."""

    progress = QtCore.pyqtSignal(str, int, int, int, int)
    finished = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)
    cancelled = QtCore.pyqtSignal()

    def __init__(self, inputs: WorkerInputs, logger: logging.Logger) -> None:
        super().__init__()
        self.inputs = inputs
        self.logger = logger
        self._tracker = _ProgressTracker(self.progress.emit)
        self._cancelled = False

    @QtCore.pyqtSlot()
    def request_cancel(self) -> None:
        self._cancelled = True

    @QtCore.pyqtSlot()
    def run(self) -> None:  # pragma: no cover - exercised via integration test
        try:
            inputs = self.inputs
            annealing_files = list(dict.fromkeys(inputs.annealing_files))
            manual_microscope = list(dict.fromkeys(inputs.manual_microscope_files))
            prep_units = len(annealing_files)
            build_units = len(annealing_files)
            self._tracker.configure(prep_units, 0, build_units, final_units=1)
            self.logger.info("Preparing support files...")
            
            def _check_cancelled() -> None:
                if self._cancelled:
                    raise BuildCancelledError()

            def _prepare_bridge() -> None:
                _check_cancelled()
                self._tracker.advance_prepare()

            _check_cancelled()
            fabrication_files, auto_microscope, video_files = collect_support_files(
                annealing_files,
                inputs.data_roots,
                progress_callback=_prepare_bridge,
                include_videos=inputs.analyse_videos,
            )
            _check_cancelled()
            self._tracker.finish_prepare()
            _check_cancelled()
            microscope_files = list(dict.fromkeys(manual_microscope + auto_microscope))
            if not inputs.analyse_videos:
                video_files = []
            analysis_units = len(microscope_files) + len(video_files)
            self._tracker.set_analysis_units(analysis_units)
            config = BuilderConfig(
                fabrication_files=fabrication_files,
                annealing_files=annealing_files,
                output_dir=inputs.output_dir,
                microscope_files=microscope_files,
                video_files=video_files,
                strain_files=inputs.strain_files,
                make_plots=bool(inputs.plot_backends),
                export_formats=inputs.export_formats,
                output_name=inputs.output_name,
                plot_backends=inputs.plot_backends,
                export_behaviour=inputs.export_behaviour,
                matplotlib_figsize=inputs.matplotlib_figsize,
                include_microscope_crops=inputs.include_microscope_crops,
                highlight_ocr_values=inputs.highlight_ocr_values,
            )
            config.output_dir.mkdir(parents=True, exist_ok=True)
            self.logger.info(
                "Starting build with %s annealing file(s)", len(config.annealing_files)
            )
            _check_cancelled()
            if fabrication_files:
                self.logger.info(
                    "Using %s fabrication spreadsheet(s)", len(fabrication_files)
                )
            if microscope_files:
                self.logger.info(
                    "Using %s microscope image(s)", len(microscope_files)
                )
            if video_files:
                self.logger.info("Using %s video file(s)", len(video_files))
            elif inputs.analyse_videos:
                self.logger.info("No fabrication videos were found for analysis.")
            else:
                self.logger.info(
                    "Fabrication video analysis disabled; skipping video metrics."
                )

            def _progress_bridge(current: int, total: int) -> None:
                _check_cancelled()
                self._tracker.build_progress(current, total)

            def _analysis_bridge(current: int, total: int) -> None:
                _check_cancelled()
                self._tracker.set_analysis_units(total)
                self._tracker.analysis_progress(current, total)

            _check_cancelled()
            result = build_database(
                config,
                logger=self.logger,
                progress_callback=_progress_bridge,
                analysis_progress_callback=_analysis_bridge,
                analysis_total=analysis_units,
                root_for_relpaths=Path.cwd(),
            )
            final_steps = max(len(result.exports), 1)
            if config.make_plots and (
                "matplotlib" in config.plot_backends or not config.plot_backends
            ):
                final_steps += 1
            if config.make_plots and "origin" in config.plot_backends:
                final_steps += 1
            final_steps += 1  # summary log
            self._tracker.set_final_units(final_steps)
            self._tracker.finish_analysis()
            self._tracker.finish_build()
            if result.exports:
                for fmt, path in result.exports.items():
                    self.logger.info("%s written to %s", fmt.upper(), path)
                    self._tracker.advance_final()
            else:
                self.logger.info("No export files were generated.")
                self._tracker.advance_final()
            if config.make_plots:
                if "matplotlib" in config.plot_backends or not config.plot_backends:
                    self.logger.info("Generated %s Matplotlib plot(s)", len(result.plot_paths))
                    self._tracker.advance_final()
                if "origin" in config.plot_backends:
                    self.logger.info(
                        "Origin plots created: %s",
                        len(result.origin_artifacts),
                    )
                    self._tracker.advance_final()
            stats = result.stats
            self.logger.info(
                "Summary: parsed=%s skipped=%s rows=%s missing_draw=%s missing_piece=%s missing_1000mA=%s missing_low_mA=%s R~=V/I failures=%s",
                stats.parsed,
                stats.skipped,
                stats.rows_built,
                stats.missing_draw,
                stats.missing_piece,
                stats.missing_high_measurement,
                stats.missing_low_measurement,
                stats.resistance_checks_failed,
            )
            self._tracker.advance_final()
            self._tracker.finalize()
            self.finished.emit(result)
        except BuildCancelledError:
            self.logger.info("Build cancelled by user.")
            self.cancelled.emit()
        except Exception as exc:  # pragma: no cover - safety net
            self.logger.exception("Build failed")
            message = str(exc) if str(exc) else exc.__class__.__name__
            self.error.emit(message)


class LegacyBuilderWindow(QtWidgets.QMainWindow):
    """Main window that orchestrates the microwire database build."""

    log_message = QtCore.pyqtSignal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Microwire Data Builder")
        self.resize(960, 720)

        self.microscope_paths: list[Path] = []
        self.annealing_paths: list[Path] = []
        self.data_roots: list[Path] = []
        self._thread: QtCore.QThread | None = None
        self._worker: BuildWorker | None = None
        self._running = False
        self._progress_start_time: float | None = None
        self._last_progress_value: int = 0
        self._last_progress_timestamp: float | None = None
        self._seconds_per_unit_ema: float | None = None
        self._last_eta_seconds: float | None = None
        self._overall_progress_current: int = 0
        self._overall_progress_total: int = 0
        self._progress_counts_text: str = ""
        self._progress_stage_text: str = ""
        self._last_logged_stage: str | None = None
        self._stage_order: tuple[str, ...] = ("prep", "analysis", "build", "final")
        self._stage_timing_history: dict[str, dict[str, float | int]] = {}
        self._init_stage_tracking()

        self._eta_timer = QtCore.QTimer(self)
        self._eta_timer.setInterval(1000)
        self._eta_timer.timeout.connect(self._on_eta_timer)

        cwd = Path.cwd()
        downloads_dir = Path.home() / "Downloads"
        if downloads_dir.exists() and downloads_dir.is_dir():
            self._default_output_dir = downloads_dir
        else:
            self._default_output_dir = cwd / "builder_output"
        self._last_microscope_dir = str(cwd)
        self._last_anneal_dir = str(cwd)
        self._last_root_dir = str(cwd)
        self._last_output_dir = str(self._default_output_dir)
        self._last_strain_dir = str(cwd)
        self.settings = QtCore.QSettings("MicrowireLab", "MicrowireDataBuilder")

        self.log_message.connect(self._append_log)

        self._build_ui()
        self._configure_logging()
        self._load_settings()
        install_standard_menu(
            self,
            help_topic="builder_database",
            console=self.log_group,
            open_file=self._add_microscope_files,
            open_folder=self._add_data_root,
        )

    # ------------------------------------------------------------------ setup
    def _init_stage_tracking(self) -> None:
        self._stage_progress: dict[str, int] = {stage: 0 for stage in self._stage_order}
        self._stage_totals: dict[str, int] = {stage: 0 for stage in self._stage_order}
        self._stage_runtime: dict[str, dict[str, object]] = {
            stage: {"start": None, "completed": False} for stage in self._stage_order
        }
        self._stage_metrics: dict[str, dict[str, object]] = {
            stage: {"last_value": 0, "last_timestamp": None, "ema": None}
            for stage in self._stage_order
        }
        self._displayed_percent = 0

    def _reset_progress_tracking(self) -> None:
        now = time.monotonic()
        self._progress_start_time = now
        self._last_progress_value = 0
        self._last_progress_timestamp = now
        self._seconds_per_unit_ema = None
        self._last_eta_seconds = None
        self._overall_progress_current = 0
        self._overall_progress_total = 0
        self._progress_counts_text = ""
        self._progress_stage_text = ""
        self._last_logged_stage = None
        self._init_stage_tracking()

    def _clear_progress_tracking(self) -> None:
        self._progress_start_time = None
        self._last_progress_value = 0
        self._last_progress_timestamp = None
        self._seconds_per_unit_ema = None
        self._last_eta_seconds = None
        self._overall_progress_current = 0
        self._overall_progress_total = 0
        self._progress_counts_text = ""
        self._progress_stage_text = ""
        self._last_logged_stage = None
        self._init_stage_tracking()

    def _reset_stage_metrics(self, stage: str) -> None:
        if stage not in self._stage_order:
            return
        metrics = self._stage_metrics.setdefault(
            stage, {"last_value": 0, "last_timestamp": None, "ema": None}
        )
        metrics["last_value"] = 0
        metrics["last_timestamp"] = None
        metrics["ema"] = None
        runtime = self._stage_runtime.setdefault(
            stage, {"start": None, "completed": False}
        )
        runtime["start"] = None
        runtime["completed"] = False

    def _persist_stage_history(self) -> None:
        if not hasattr(self, "settings"):
            return
        try:
            payload = json.dumps(self._stage_timing_history)
        except TypeError:
            return
        self.settings.setValue("stage_timing", payload)
        self.settings.sync()

    def _record_stage_history(self, stage: str, elapsed: float, units: int) -> None:
        if units <= 0 or elapsed <= 0:
            return
        record = self._stage_timing_history.setdefault(
            stage,
            {"seconds": 0.0, "units": 0, "ema": None, "samples": 0},
        )
        seconds = max(float(record.get("seconds", 0.0)), 0.0) + float(elapsed)
        units_done = max(int(record.get("units", 0)), 0) + int(units)
        record["seconds"] = seconds
        record["units"] = units_done
        average = float(elapsed) / float(units)
        ema = record.get("ema")
        if isinstance(ema, (int, float)) and ema > 0:
            record["ema"] = max((float(ema) * 0.7) + (average * 0.3), 0.0)
        else:
            record["ema"] = max(average, 0.0)
        record["samples"] = int(record.get("samples", 0)) + 1
        self._persist_stage_history()

    def _global_average_rate(self) -> float | None:
        ema_sum = 0.0
        ema_weight = 0.0
        total_seconds = 0.0
        total_units = 0
        for record in self._stage_timing_history.values():
            if not isinstance(record, dict):
                continue
            ema = record.get("ema")
            samples = max(int(record.get("samples", 0)), 0)
            if isinstance(ema, (int, float)) and ema > 0:
                ema_sum += float(ema) * max(samples, 1)
                ema_weight += max(samples, 1)
            seconds = float(record.get("seconds", 0.0))
            units = int(record.get("units", 0))
            total_seconds += max(seconds, 0.0)
            total_units += max(units, 0)
        if ema_weight > 0:
            return ema_sum / ema_weight
        if total_seconds > 0 and total_units > 0:
            return total_seconds / total_units
        return None

    def _estimate_remaining_seconds(
        self,
        now: float,
        active_stage: str | None,
        overall_current: int,
        overall_total: int,
    ) -> float | None:
        remaining = 0.0
        have_estimate = False
        global_rate = self._global_average_rate()

        def _push(rate_list: list[tuple[float, float]], value: object, weight: float) -> None:
            if isinstance(value, (int, float)) and value > 0 and weight > 0:
                rate_list.append((float(value), weight))

        for stage in self._stage_order:
            total_units = max(int(self._stage_totals.get(stage, 0)), 0)
            progress_units = min(max(int(self._stage_progress.get(stage, 0)), 0), total_units)
            remaining_units = total_units - progress_units
            if remaining_units <= 0:
                continue

            rates: list[tuple[float, float]] = []
            metrics = self._stage_metrics.get(stage, {})
            stage_ema = metrics.get("ema") if isinstance(metrics, dict) else None
            _push(rates, stage_ema, 0.3)

            history = self._stage_timing_history.get(stage)
            history_rate: float | None = None
            history_weight = 0.0
            if isinstance(history, dict):
                hist_ema = history.get("ema")
                hist_seconds = float(history.get("seconds", 0.0))
                hist_units = int(history.get("units", 0))
                samples = max(int(history.get("samples", 0)), 0)
                if isinstance(hist_ema, (int, float)) and hist_ema > 0:
                    history_rate = float(hist_ema)
                elif hist_seconds > 0 and hist_units > 0:
                    history_rate = hist_seconds / hist_units
                if history_rate is not None:
                    history_weight = 0.45 + min(samples, 10) * 0.035
                    _push(rates, history_rate, history_weight)

            runtime = self._stage_runtime.get(stage, {})
            if (
                stage == active_stage
                and isinstance(runtime, dict)
                and runtime.get("start") is not None
            ):
                start_time = float(runtime["start"])
                elapsed_stage = max(now - start_time, 0.0)
                completed_units = max(int(self._stage_progress.get(stage, 0)), 0)
                if elapsed_stage > 0 and completed_units > 0:
                    runtime_rate = elapsed_stage / completed_units
                    _push(rates, runtime_rate, 0.5)

            if stage == active_stage and self._seconds_per_unit_ema:
                _push(rates, self._seconds_per_unit_ema, 0.3)

            if not rates and global_rate is not None:
                _push(rates, global_rate, 0.5)

            if not rates:
                continue

            have_estimate = True
            total_weight = sum(weight for _, weight in rates)
            if total_weight <= 0:
                continue
            weighted = sum(rate * weight for rate, weight in rates) / total_weight
            sorted_values = sorted(rate for rate, _ in rates)
            if not sorted_values:
                continue
            mid = len(sorted_values) // 2
            if len(sorted_values) % 2:
                median = sorted_values[mid]
            else:
                median = (sorted_values[mid - 1] + sorted_values[mid]) / 2
            rate = max(weighted, median)
            if history_rate is not None:
                rate = max(rate, history_rate * 0.85)
            remaining += remaining_units * rate

        if have_estimate:
            return remaining if remaining > 0 else 0.0
        start_time = self._progress_start_time
        if start_time is None or overall_current <= 0:
            return None
        elapsed = max(now - start_time, 0.0)
        remaining_units = max(overall_total - overall_current, 0)
        return (elapsed / overall_current) * remaining_units if overall_current else None

    @staticmethod
    def _format_eta(seconds: float) -> str:
        if seconds < 0:
            seconds = 0
        total_seconds = int(round(seconds))
        if total_seconds < 1:
            return "<1s remaining"
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        parts: list[str] = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if secs or not parts:
            parts.append(f"{secs}s")
        return " ".join(parts) + " remaining"

    def _smooth_eta(self, seconds: float) -> float:
        seconds = max(float(seconds), 0.0)
        previous = self._last_eta_seconds
        if previous is None:
            filtered = seconds
        else:
            if seconds > previous:
                alpha = 0.65
            else:
                alpha = 0.2
            filtered = previous + (seconds - previous) * alpha
        self._last_eta_seconds = max(filtered, 0.0)
        return self._last_eta_seconds

    def _on_eta_timer(self) -> None:
        self._update_eta_display()

    def _resolve_active_stage(self) -> str | None:
        for key in self._stage_order:
            total_units = max(int(self._stage_totals.get(key, 0)), 0)
            progress_units = min(max(int(self._stage_progress.get(key, 0)), 0), total_units)
            if total_units > 0 and progress_units < total_units:
                return key
        return None

    def _update_eta_display(self, now: Optional[float] = None) -> None:
        if not self._running:
            return
        if now is None:
            now = time.monotonic()
        current = max(int(self._overall_progress_current), 0)
        total = max(int(self._overall_progress_total), 1)
        remaining_units = max(total - current, 0)
        eta_text: Optional[str] = None
        if remaining_units <= 0:
            self._last_eta_seconds = None
            if current:
                eta_text = "Finishing..."
        else:
            active_stage = self._resolve_active_stage()
            remaining_seconds = self._estimate_remaining_seconds(now, active_stage, current, total)
            if remaining_seconds is not None and math.isfinite(remaining_seconds):
                smoothed = self._smooth_eta(remaining_seconds)
                eta_text = self._format_eta(smoothed)
            elif current > 0:
                eta_text = "Estimating..."
                self._last_eta_seconds = None

        parts: list[str] = []
        if self._progress_stage_text:
            parts.append(self._progress_stage_text)
        if self._progress_counts_text:
            parts.append(self._progress_counts_text)
        if eta_text:
            parts.append(eta_text)
        label_text = " • ".join(parts) if parts else "Working..."
        if label_text != self.progress_label.text():
            self.progress_label.setText(label_text)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)

        left_layout = QtWidgets.QVBoxLayout()
        left_layout.setSpacing(10)
        right_layout = QtWidgets.QVBoxLayout()
        right_layout.setSpacing(10)

        main_layout.addLayout(left_layout, 2)
        main_layout.addLayout(right_layout, 3)

        # Data roots
        self.root_group = QtWidgets.QGroupBox("Microwire data folder")
        root_layout = QtWidgets.QVBoxLayout(self.root_group)
        self.root_list = QtWidgets.QListWidget()
        self.root_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.root_group.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.root_list.setMaximumHeight(110)
        root_layout.addWidget(self.root_list)
        root_buttons = QtWidgets.QHBoxLayout()
        root_add = QtWidgets.QPushButton("Add folder...")
        root_add.clicked.connect(self._add_data_root)
        root_buttons.addWidget(root_add)
        root_clear = QtWidgets.QPushButton("Clear")
        root_clear.clicked.connect(self._clear_data_roots)
        root_buttons.addWidget(root_clear)
        root_buttons.addStretch(1)
        root_layout.addLayout(root_buttons)
        right_layout.addWidget(self.root_group)

        # Annealing inputs
        self.anneal_group = QtWidgets.QGroupBox("Current-annealing files (.txt)")
        anneal_layout = QtWidgets.QVBoxLayout(self.anneal_group)
        self.anneal_list = QtWidgets.QListWidget()
        self.anneal_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        anneal_layout.addWidget(self.anneal_list)

        anneal_buttons = QtWidgets.QHBoxLayout()
        anneal_add_files = QtWidgets.QPushButton("Add files...")
        anneal_add_files.clicked.connect(self._add_anneal_files)
        anneal_buttons.addWidget(anneal_add_files)
        anneal_add_folder = QtWidgets.QPushButton("Add folder...")
        anneal_add_folder.clicked.connect(self._add_anneal_folder)
        anneal_buttons.addWidget(anneal_add_folder)
        anneal_clear = QtWidgets.QPushButton("Clear")
        anneal_clear.clicked.connect(self._clear_anneal)
        anneal_buttons.addWidget(anneal_clear)
        anneal_buttons.addStretch(1)
        anneal_layout.addLayout(anneal_buttons)

        self.anneal_recursive = QtWidgets.QCheckBox("Recursive scan")
        self.anneal_recursive.setChecked(True)
        anneal_layout.addWidget(self.anneal_recursive)
        right_layout.addWidget(self.anneal_group, 1)

        # Microscope inputs
        self.microscope_group = QtWidgets.QGroupBox("Microscope images")
        micro_layout = QtWidgets.QVBoxLayout(self.microscope_group)
        self.microscope_list = QtWidgets.QListWidget()
        self.microscope_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        micro_layout.addWidget(self.microscope_list)
        micro_buttons = QtWidgets.QHBoxLayout()
        micro_add_files = QtWidgets.QPushButton("Add files...")
        micro_add_files.clicked.connect(self._add_microscope_files)
        micro_buttons.addWidget(micro_add_files)
        micro_add_folder = QtWidgets.QPushButton("Add folder...")
        micro_add_folder.clicked.connect(self._add_microscope_folder)
        micro_buttons.addWidget(micro_add_folder)
        micro_clear = QtWidgets.QPushButton("Clear")
        micro_clear.clicked.connect(self._clear_microscope)
        micro_buttons.addWidget(micro_clear)
        micro_buttons.addStretch(1)
        micro_layout.addLayout(micro_buttons)
        self.microscope_recursive = QtWidgets.QCheckBox("Recursive scan")
        self.microscope_recursive.setChecked(True)
        micro_layout.addWidget(self.microscope_recursive)
        right_layout.addWidget(self.microscope_group, 1)

        # Strain worksheet
        self.strain_group = QtWidgets.QGroupBox("Strain worksheet")
        strain_layout = QtWidgets.QHBoxLayout(self.strain_group)
        strain_layout.setContentsMargins(8, 8, 8, 8)
        self.strain_edit = QtWidgets.QLineEdit()
        self.strain_edit.setPlaceholderText("Select an Excel worksheet with strain data…")
        strain_layout.addWidget(self.strain_edit, 1)
        self.strain_button = QtWidgets.QPushButton("Browse…")
        self.strain_button.clicked.connect(self._select_strain_file)
        strain_layout.addWidget(self.strain_button)
        self.clear_strain_button = QtWidgets.QPushButton("Clear")
        self.clear_strain_button.clicked.connect(self._clear_strain_file)
        strain_layout.addWidget(self.clear_strain_button)
        right_layout.addWidget(self.strain_group)

        # Options
        self.options_group = QtWidgets.QGroupBox("Options")
        options_layout = QtWidgets.QVBoxLayout(self.options_group)
        self.plot_matplotlib_check = QtWidgets.QCheckBox("Matplotlib plots (PNG)")
        self.plot_matplotlib_check.setChecked(True)
        self.plot_matplotlib_check.stateChanged.connect(self._save_settings)
        options_layout.addWidget(self.plot_matplotlib_check)
        self.plot_origin_check = QtWidgets.QCheckBox("Origin plots")
        self.plot_origin_check.stateChanged.connect(self._save_settings)
        options_layout.addWidget(self.plot_origin_check)
        self.export_csv_check = QtWidgets.QCheckBox("Export CSV")
        self.export_csv_check.setChecked(True)
        self.export_csv_check.stateChanged.connect(self._save_settings)
        options_layout.addWidget(self.export_csv_check)
        self.export_excel_check = QtWidgets.QCheckBox("Export Excel")
        self.export_excel_check.stateChanged.connect(self._save_settings)
        options_layout.addWidget(self.export_excel_check)

        microscope_group = QtWidgets.QGroupBox("Microscope review")
        microscope_layout = QtWidgets.QVBoxLayout(microscope_group)
        microscope_layout.setContentsMargins(8, 8, 8, 8)
        self.include_crops_check = QtWidgets.QCheckBox("Attach microscope crops to Excel")
        self.include_crops_check.stateChanged.connect(self._save_settings)
        self.include_crops_check.setToolTip(
            "Add cropped microscope images in new columns next to the d and D values"
        )
        with QtCore.QSignalBlocker(self.include_crops_check):
            self.include_crops_check.setChecked(True)
        microscope_layout.addWidget(self.include_crops_check)
        self.highlight_ocr_check = QtWidgets.QCheckBox("Highlight OCR-sourced values")
        self.highlight_ocr_check.stateChanged.connect(self._save_settings)
        self.highlight_ocr_check.setToolTip(
            "Tint spreadsheet cells where the value was filled from OCR instead of fabrication spreadsheets"
        )
        with QtCore.QSignalBlocker(self.highlight_ocr_check):
            self.highlight_ocr_check.setChecked(True)
        microscope_layout.addWidget(self.highlight_ocr_check)
        options_layout.addWidget(microscope_group)

        self.video_metrics_check = QtWidgets.QCheckBox("Extract fabrication metrics from videos")
        self.video_metrics_check.setChecked(True)
        self.video_metrics_check.stateChanged.connect(self._save_settings)
        self.video_metrics_check.setToolTip(
            "Sample fabrication videos to OCR winding speed, glass feed, temperature, and underpressure values"
        )
        options_layout.addWidget(self.video_metrics_check)

        figure_size_form = QtWidgets.QFormLayout()
        figure_size_form.setHorizontalSpacing(8)
        figure_size_form.setVerticalSpacing(4)
        self.figure_width_spin = QtWidgets.QDoubleSpinBox()
        self.figure_width_spin.setRange(20.0, 400.0)
        self.figure_width_spin.setDecimals(1)
        self.figure_width_spin.setSingleStep(5.0)
        self.figure_width_spin.setValue(FIGURE_WIDTH_DEFAULT_MM)
        self.figure_width_spin.valueChanged.connect(self._save_settings)
        figure_size_form.addRow("Figure width (mm)", self.figure_width_spin)
        self.figure_height_spin = QtWidgets.QDoubleSpinBox()
        self.figure_height_spin.setRange(20.0, 250.0)
        self.figure_height_spin.setDecimals(1)
        self.figure_height_spin.setSingleStep(5.0)
        self.figure_height_spin.setValue(FIGURE_HEIGHT_DEFAULT_MM)
        self.figure_height_spin.valueChanged.connect(self._save_settings)
        figure_size_form.addRow("Figure height (mm)", self.figure_height_spin)
        options_layout.addLayout(figure_size_form)
        left_layout.addWidget(self.options_group)

        # Output directory
        self.output_group = QtWidgets.QGroupBox("Output")
        output_layout = QtWidgets.QGridLayout(self.output_group)
        output_layout.setHorizontalSpacing(8)
        output_layout.setVerticalSpacing(6)
        output_label = QtWidgets.QLabel("Directory:")
        output_layout.addWidget(output_label, 0, 0)
        self.output_edit = QtWidgets.QLineEdit(str(self._default_output_dir))
        self.output_edit.editingFinished.connect(self._save_settings)
        output_layout.addWidget(self.output_edit, 0, 1)
        self.output_button = QtWidgets.QPushButton("Browse...")
        self.output_button.clicked.connect(self._select_output_dir)
        output_layout.addWidget(self.output_button, 0, 2)
        name_label = QtWidgets.QLabel("File name:")
        output_layout.addWidget(name_label, 1, 0)
        self.output_name_edit = QtWidgets.QLineEdit(DEFAULT_OUTPUT_NAME)
        self.output_name_edit.editingFinished.connect(self._save_settings)
        output_layout.addWidget(self.output_name_edit, 1, 1)
        output_layout.setColumnStretch(1, 1)
        left_layout.addWidget(self.output_group)

        # Progress row
        progress_row = QtWidgets.QHBoxLayout()
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_row.addWidget(self.progress_bar, stretch=1)
        self.progress_label = QtWidgets.QLabel("Idle")
        progress_row.addWidget(self.progress_label)
        left_layout.addLayout(progress_row)

        # Log view
        self.log_group = QtWidgets.QGroupBox("Log")
        log_layout = QtWidgets.QVBoxLayout(self.log_group)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        log_layout.addWidget(self.log_view)
        left_layout.addWidget(self.log_group, stretch=1)

        # Run button
        run_row = QtWidgets.QHBoxLayout()
        run_row.addStretch(1)
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_build)
        run_row.addWidget(self.cancel_button)
        self.run_button = QtWidgets.QPushButton("Run")
        self.run_button.clicked.connect(self.start_build)
        run_row.addWidget(self.run_button)
        left_layout.addLayout(run_row)

    def _configure_logging(self) -> None:
        self.logger = logging.getLogger(LOGGER_NAME)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self._log_handler = QtLogHandler(self.log_message.emit)
        self._log_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        if self._log_handler not in self.logger.handlers:
            self.logger.addHandler(self._log_handler)

    def _load_settings(self) -> None:
        def _decode_paths(value: object) -> list[Path]:
            if isinstance(value, list | tuple):
                return [Path(str(item)) for item in value]
            if isinstance(value, str):
                if not value:
                    return []
                try:
                    data = json.loads(value)
                    if isinstance(data, list):
                        return [Path(str(item)) for item in data]
                except json.JSONDecodeError:
                    items = [segment.strip() for segment in value.splitlines() if segment.strip()]
                    return [Path(item) for item in items]
            return []

        def _read_bool(key: str, default: bool) -> bool:
            value = self.settings.value(key, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "y"}:
                    return True
                if lowered in {"false", "0", "no", "n"}:
                    return False
            return default

        def _read_float(key: str, default: float) -> float:
            value = self.settings.value(key, default)
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        self.annealing_paths = _decode_paths(self.settings.value("annealing_paths", ""))
        self._update_list_widget(self.anneal_list, self.annealing_paths)
        self.microscope_paths = _decode_paths(self.settings.value("microscope_paths", ""))
        self._update_list_widget(self.microscope_list, self.microscope_paths)
        self.data_roots = _decode_paths(self.settings.value("data_roots", ""))
        self._update_list_widget(self.root_list, self.data_roots)

        with QtCore.QSignalBlocker(self.plot_matplotlib_check):
            self.plot_matplotlib_check.setChecked(_read_bool("plot_matplotlib", True))
        with QtCore.QSignalBlocker(self.plot_origin_check):
            self.plot_origin_check.setChecked(_read_bool("plot_origin", False))
        with QtCore.QSignalBlocker(self.export_csv_check):
            self.export_csv_check.setChecked(_read_bool("export_csv", True))
        with QtCore.QSignalBlocker(self.export_excel_check):
            self.export_excel_check.setChecked(_read_bool("export_excel", False))
        with QtCore.QSignalBlocker(self.include_crops_check):
            self.include_crops_check.setChecked(_read_bool("include_microscope_crops", True))
        with QtCore.QSignalBlocker(self.highlight_ocr_check):
            self.highlight_ocr_check.setChecked(_read_bool("highlight_ocr_values", True))
        with QtCore.QSignalBlocker(self.video_metrics_check):
            self.video_metrics_check.setChecked(_read_bool("analyse_videos", True))
        if hasattr(self, "microscope_recursive"):
            with QtCore.QSignalBlocker(self.microscope_recursive):
                self.microscope_recursive.setChecked(_read_bool("microscope_recursive", True))

        width_value = _read_float("figure_width", FIGURE_WIDTH_DEFAULT_MM)
        height_value = _read_float("figure_height", FIGURE_HEIGHT_DEFAULT_MM)
        if width_value <= 20.0 and height_value <= 20.0:
            width_value *= 25.4
            height_value *= 25.4
        with QtCore.QSignalBlocker(self.figure_width_spin):
            self.figure_width_spin.setValue(width_value)
        with QtCore.QSignalBlocker(self.figure_height_spin):
            self.figure_height_spin.setValue(height_value)

        output_dir_value = self.settings.value("output_dir", "")
        if isinstance(output_dir_value, str) and output_dir_value.strip():
            self.output_edit.setText(output_dir_value)
            self._last_output_dir = output_dir_value

        output_name_value = self.settings.value("output_name", "")
        if isinstance(output_name_value, str) and output_name_value.strip():
            self.output_name_edit.setText(output_name_value)

        strain_path_value = self.settings.value("strain_path", "")
        if isinstance(strain_path_value, str) and strain_path_value.strip():
            self.strain_edit.setText(strain_path_value)
            try:
                self._last_strain_dir = str(Path(strain_path_value).expanduser().parent)
            except Exception:
                pass

        last_microscope = self.settings.value("last_microscope_dir", "")
        if isinstance(last_microscope, str) and last_microscope.strip():
            self._last_microscope_dir = last_microscope
        last_anneal = self.settings.value("last_anneal_dir", "")
        if isinstance(last_anneal, str) and last_anneal.strip():
            self._last_anneal_dir = last_anneal
        last_root = self.settings.value("last_root_dir", "")
        if isinstance(last_root, str) and last_root.strip():
            self._last_root_dir = last_root
        last_output = self.settings.value("last_output_dir", "")
        if isinstance(last_output, str) and last_output.strip():
            self._last_output_dir = last_output
        last_strain = self.settings.value("last_strain_dir", "")
        if isinstance(last_strain, str) and last_strain.strip():
            self._last_strain_dir = last_strain

        timing_value = self.settings.value("stage_timing", "")
        if isinstance(timing_value, str) and timing_value.strip():
            try:
                stored = json.loads(timing_value)
            except json.JSONDecodeError:
                stored = {}
            if isinstance(stored, dict):
                for key, record in stored.items():
                    if not isinstance(record, dict):
                        continue
                    seconds = float(record.get("seconds", 0.0))
                    units = int(record.get("units", 0))
                    if seconds > 0 and units > 0:
                        ema_value = record.get("ema")
                        samples_value = record.get("samples", 0)
                        if isinstance(ema_value, (int, float)) and ema_value > 0:
                            ema = float(ema_value)
                        else:
                            ema = seconds / units
                        samples = int(samples_value) if isinstance(samples_value, (int, float)) else 0
                        if samples <= 0 and ema > 0:
                            samples = 1
                        self._stage_timing_history[key] = {
                            "seconds": seconds,
                            "units": units,
                            "ema": ema,
                            "samples": max(samples, 0),
                        }

    def _save_settings(self) -> None:
        if not hasattr(self, "settings"):
            return
        self.settings.setValue("annealing_paths", json.dumps([str(p) for p in self.annealing_paths]))
        self.settings.setValue("microscope_paths", json.dumps([str(p) for p in self.microscope_paths]))
        self.settings.setValue("data_roots", json.dumps([str(p) for p in self.data_roots]))
        self.settings.setValue("output_dir", self.output_edit.text())
        self.settings.setValue("plot_matplotlib", self.plot_matplotlib_check.isChecked())
        self.settings.setValue("plot_origin", self.plot_origin_check.isChecked())
        self.settings.setValue("export_csv", self.export_csv_check.isChecked())
        self.settings.setValue("export_excel", self.export_excel_check.isChecked())
        self.settings.setValue("include_microscope_crops", self.include_crops_check.isChecked())
        self.settings.setValue("highlight_ocr_values", self.highlight_ocr_check.isChecked())
        self.settings.setValue("analyse_videos", self.video_metrics_check.isChecked())
        self.settings.setValue("figure_width", self.figure_width_spin.value())
        self.settings.setValue("figure_height", self.figure_height_spin.value())
        self.settings.setValue("output_name", self.output_name_edit.text())
        self.settings.setValue("strain_path", self.strain_edit.text())
        self.settings.setValue("last_microscope_dir", self._last_microscope_dir)
        self.settings.setValue("last_anneal_dir", self._last_anneal_dir)
        self.settings.setValue("last_root_dir", self._last_root_dir)
        self.settings.setValue("last_output_dir", self._last_output_dir)
        self.settings.setValue("last_strain_dir", self._last_strain_dir)
        self.settings.setValue("microscope_recursive", self.microscope_recursive.isChecked())
        self.settings.setValue("stage_timing", json.dumps(self._stage_timing_history))
        self.settings.sync()

    # ------------------------------------------------------------------ helpers
    def _extend_paths(self, attr: str, paths: Iterable[Path]) -> None:
        current: list[Path] = getattr(self, attr)
        combined = list(dict.fromkeys(current + [Path(p) for p in paths]))
        setattr(self, attr, combined)

    def _update_list_widget(self, widget: QtWidgets.QListWidget, items: Iterable[Path]) -> None:
        widget.clear()
        for text in sorted({str(Path(p)) for p in items}):
            widget.addItem(text)

    def _is_microscope_candidate(self, path: Path) -> bool:
        return is_microscope_candidate(path)

    def _collect_support_files(
        self, annealing_files: list[Path]
    ) -> tuple[list[Path], list[Path], list[Path]]:
        include_videos = getattr(self, "video_metrics_check", None)
        analyse_videos = True
        if isinstance(include_videos, QtWidgets.QCheckBox):
            analyse_videos = include_videos.isChecked()
        return collect_support_files(
            annealing_files,
            self.data_roots,
            include_videos=analyse_videos,
        )

    def _add_anneal_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select current-annealing files",
            self._last_anneal_dir,
            "Text files (*.txt)",
        )
        if not files:
            return
        self._last_anneal_dir = str(Path(files[0]).parent)
        self._extend_paths("annealing_paths", (Path(f) for f in files))
        self._update_list_widget(self.anneal_list, self.annealing_paths)
        self._save_settings()

    def _add_anneal_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select folder with current-annealing files",
            self._last_anneal_dir,
        )
        if not folder:
            return
        root = Path(folder)
        iterator = root.rglob("*.txt") if self.anneal_recursive.isChecked() else root.glob("*.txt")
        files = [p for p in iterator if p.is_file()]
        if not files:
            QtWidgets.QMessageBox.information(self, "Microwire Data Builder", "No text files were found in that folder.")
            return
        self._last_anneal_dir = folder
        self._extend_paths("annealing_paths", files)
        self._update_list_widget(self.anneal_list, self.annealing_paths)
        self._save_settings()

    def _clear_anneal(self) -> None:
        self.annealing_paths = []
        self.anneal_list.clear()
        self._save_settings()

    def _add_microscope_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select microscope images",
            self._last_microscope_dir,
            "Image files (*.jpg *.jpeg *.png *.tif *.tiff *.bmp)",
        )
        if not files:
            return
        self._last_microscope_dir = str(Path(files[0]).parent)
        candidates = [Path(f) for f in files if self._is_microscope_candidate(Path(f))]
        if not candidates:
            QtWidgets.QMessageBox.information(
                self,
                "Microwire Data Builder",
                "No matching microscope images were selected.",
            )
            return
        self._extend_paths("microscope_paths", candidates)
        self._update_list_widget(self.microscope_list, self.microscope_paths)
        self._save_settings()

    def _add_microscope_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select folder with microscope images",
            self._last_microscope_dir,
        )
        if not folder:
            return
        root = Path(folder)
        iterator = root.rglob('*') if self.microscope_recursive.isChecked() else root.glob('*')
        files = [p for p in iterator if p.is_file() and self._is_microscope_candidate(p)]
        if not files:
            QtWidgets.QMessageBox.information(
                self,
                "Microwire Data Builder",
                "No microscope images were found in that folder.",
            )
            return
        self._last_microscope_dir = folder
        self._extend_paths("microscope_paths", files)
        self._update_list_widget(self.microscope_list, self.microscope_paths)
        self._save_settings()

    def _clear_microscope(self) -> None:
        self.microscope_paths = []
        self.microscope_list.clear()
        self._save_settings()

    def _select_strain_file(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select strain worksheet",
            self._last_strain_dir,
            "Excel files (*.xlsx *.xlsm *.xls)",
        )
        if not filename:
            return
        path = Path(filename)
        self._last_strain_dir = str(path.parent)
        self.strain_edit.setText(str(path))
        self._save_settings()

    def _clear_strain_file(self) -> None:
        self.strain_edit.clear()
        self._save_settings()

    def _add_data_root(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select microwire data folder",
            self._last_root_dir,
        )
        if not folder:
            return
        root = Path(folder)
        if not root.exists():
            QtWidgets.QMessageBox.warning(self, "Microwire Data Builder", "The selected folder does not exist.")
            return
        self._last_root_dir = folder
        self.data_roots = [root]
        self._update_list_widget(self.root_list, self.data_roots)
        self._ingest_data_root(root, announce=True)
        self._save_settings()

    def _clear_data_roots(self) -> None:
        self.data_roots = []
        self.root_list.clear()
        self._save_settings()
    def _clear_data_roots(self) -> None:
        self.data_roots = []
        self.root_list.clear()
        self._save_settings()

    def _ingest_data_root(self, root: Path, announce: bool = False) -> None:
        if announce and hasattr(self, 'logger'):
            self.logger.info('Microwire data folder set to %s', root)
    def _select_output_dir(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select output directory",
            self.output_edit.text() or self._last_output_dir,
        )
        if directory:
            self._last_output_dir = directory
            self.output_edit.setText(directory)
            self._save_settings()

    def _check_origin_available(self) -> tuple[bool, str]:
        try:
            import originpro  # type: ignore  # noqa: F401
        except Exception as exc:
            message = str(exc) if str(exc) else exc.__class__.__name__
            return False, message
        return True, ""

    def _prompt_overwrite(self, path: Path) -> str | None:
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("File exists")
        msg.setIcon(QtWidgets.QMessageBox.Icon.Question)
        msg.setText(f"'{path.name}' already exists.")
        msg.setInformativeText("Choose how to continue:")
        replace_btn = msg.addButton("Replace", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        update_btn = msg.addButton("Update", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        continue_btn = msg.addButton("Append", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        cancel_btn = msg.addButton("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is cancel_btn:
            return None
        if clicked is update_btn:
            return "update"
        if clicked is continue_btn:
            return "append"
        return "replace"

    def _preferred_export_path(self, exports: dict[str, Path]) -> Path | None:
        for key in ("excel", "csv"):
            candidate = exports.get(key)
            if isinstance(candidate, Path):
                return candidate
        for candidate in exports.values():
            if isinstance(candidate, Path):
                return candidate
        return None

    def _open_path(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if not resolved.exists():
            QtWidgets.QMessageBox.warning(
                self,
                "Microwire Data Builder",
                f"{resolved} could not be found.",
            )
            return
        url = QtCore.QUrl.fromLocalFile(str(resolved))
        if not QtGui.QDesktopServices.openUrl(url):
            QtWidgets.QMessageBox.warning(
                self,
                "Microwire Data Builder",
                "Unable to open the exported file.",
            )

    def _set_running(self, running: bool) -> None:
        self._running = running
        for widget in (
            self.root_group,
            self.anneal_group,
            self.microscope_group,
            self.options_group,
            self.output_group,
        ):
            widget.setEnabled(not running)
        self.run_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        if running:
            if not self._eta_timer.isActive():
                self._eta_timer.start()
            self._reset_progress_tracking()
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setValue(0)
            self._progress_stage_text = _STAGE_LABELS.get("prep", "Preparing...")
            self._update_eta_display()
        else:
            self._eta_timer.stop()
            self._clear_progress_tracking()
            if self.progress_bar.maximum() == 0 and self.progress_bar.minimum() == 0:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(0)
            if self.progress_label.text() not in {"Complete", "Failed"}:
                self.progress_label.setText("Idle")

    # ------------------------------------------------------------------ build orchestration
    def cancel_build(self) -> None:
        if not self._running:
            return
        if self._worker is None:
            self._set_running(False)
            self.progress_bar.setValue(0)
            self.progress_label.setText("Cancelled")
            return
        self.logger.info("Cancellation requested; attempting to stop the build...")
        self.cancel_button.setEnabled(False)
        self.progress_label.setText("Cancelling...")
        QtCore.QMetaObject.invokeMethod(
            self._worker,
            "request_cancel",
            QtCore.Qt.ConnectionType.QueuedConnection,
        )

    def start_build(self) -> None:
        if self._running:
            return
        output_dir_text = self.output_edit.text().strip()
        if not output_dir_text:
            QtWidgets.QMessageBox.warning(self, "Microwire Data Builder", "Please choose an output directory.")
            return
        output_dir = Path(output_dir_text).expanduser()
        export_formats: list[str] = []
        if self.export_csv_check.isChecked():
            export_formats.append("csv")
        if self.export_excel_check.isChecked():
            export_formats.append("excel")
        if not export_formats:
            QtWidgets.QMessageBox.warning(
                self,
                "Microwire Data Builder",
                "Please select at least one export format.",
            )
            return
        output_name_text = self.output_name_edit.text().strip()
        if not output_name_text:
            QtWidgets.QMessageBox.warning(
                self,
                "Microwire Data Builder",
                "Please enter a base file name.",
            )
            return
        output_name = _normalise_output_name(output_name_text)
        if output_name != output_name_text:
            self.output_name_edit.setText(output_name)

        plot_backends: list[str] = []
        if self.plot_matplotlib_check.isChecked():
            plot_backends.append("matplotlib")
        if self.plot_origin_check.isChecked():
            origin_ok, origin_error = self._check_origin_available()
            if origin_ok:
                plot_backends.append("origin")
            else:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Microwire Data Builder",
                    "Origin plotting is unavailable.\n\n" + origin_error,
                )
                with QtCore.QSignalBlocker(self.plot_origin_check):
                    self.plot_origin_check.setChecked(False)

        behaviours: dict[str, str] = {}
        for fmt in export_formats:
            extension = "csv" if fmt.lower() == "csv" else "xlsx"
            target_path = output_dir / f"{output_name}.{extension}"
            if target_path.exists():
                action = self._prompt_overwrite(target_path)
                if action is None:
                    return
                behaviours[fmt.lower()] = action
            else:
                behaviours[fmt.lower()] = "replace"

        annealing_files = list(dict.fromkeys(self.annealing_paths))
        requires_measurements = any(action != "update" for action in behaviours.values())
        if requires_measurements and not annealing_files:
            QtWidgets.QMessageBox.warning(
                self,
                "Microwire Data Builder",
                "Please add at least one annealing file.",
            )
            return

        strain_files: list[Path] = []
        strain_text = self.strain_edit.text().strip()
        if strain_text:
            strain_files.append(Path(strain_text).expanduser())
        if not requires_measurements and not strain_files:
            QtWidgets.QMessageBox.information(
                self,
                "Microwire Data Builder",
                "No strain worksheet selected; nothing to update.",
            )
            return

        worker_inputs = WorkerInputs(
            annealing_files=annealing_files,
            manual_microscope_files=list(dict.fromkeys(self.microscope_paths)),
            data_roots=list(dict.fromkeys(self.data_roots)),
            output_dir=output_dir,
            output_name=output_name,
            export_formats=tuple(export_formats),
            plot_backends=tuple(plot_backends),
            export_behaviour=behaviours,
            matplotlib_figsize=(
                float(self.figure_width_spin.value()) / 25.4,
                float(self.figure_height_spin.value()) / 25.4,
            ),
            include_microscope_crops=self.include_crops_check.isChecked(),
            highlight_ocr_values=self.highlight_ocr_check.isChecked(),
            analyse_videos=self.video_metrics_check.isChecked(),
            strain_files=strain_files,
        )
        self._save_settings()
        self._set_running(True)
        self.log_view.clear()
        self.logger.info(
            "Queued build for %s annealing measurement(s)",
            len(worker_inputs.annealing_files),
        )
        self._start_worker(worker_inputs)

    def _start_worker(self, inputs: WorkerInputs) -> None:
        self._thread = QtCore.QThread(self)
        self._worker = BuildWorker(inputs, self.logger)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._update_progress)
        self._worker.finished.connect(self._handle_finished)
        self._worker.error.connect(self._handle_failed)
        self._worker.cancelled.connect(self._handle_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _update_progress(
        self,
        stage: str,
        stage_value: int,
        stage_total: int,
        current: int,
        total: int,
    ) -> None:
        total = max(total, 1)
        if self._progress_start_time is None:
            self._reset_progress_tracking()
        if current < self._last_progress_value:
            self._reset_progress_tracking()
        now = time.monotonic()
        if current > self._last_progress_value:
            delta_units = current - self._last_progress_value
            last_timestamp = self._last_progress_timestamp
            if last_timestamp is not None and delta_units > 0:
                delta_time = max(now - last_timestamp, 0.0)
                if delta_time > 0:
                    instantaneous = delta_time / delta_units
                    ema = self._seconds_per_unit_ema
                    if ema is None:
                        ema = instantaneous
                    elif instantaneous >= ema:
                        ema = (ema * 0.35) + (instantaneous * 0.65)
                    else:
                        ema = (ema * 0.9) + (instantaneous * 0.1)
                    self._seconds_per_unit_ema = max(ema, 0.0)
            self._last_progress_value = current
            self._last_progress_timestamp = now

        if stage not in self._stage_totals:
            self._stage_order += (stage,)
            self._stage_totals[stage] = max(stage_total, 0)
            clamped_value = max(stage_value, 0)
            if self._stage_totals[stage] > 0:
                clamped_value = min(clamped_value, self._stage_totals[stage])
            self._stage_progress[stage] = clamped_value
            self._stage_runtime[stage] = {"start": None, "completed": False}
            self._stage_metrics[stage] = {
                "last_value": 0,
                "last_timestamp": None,
                "ema": None,
            }
        else:
            self._stage_totals[stage] = max(stage_total, 0)
            previous_value = self._stage_progress.get(stage, 0)
            if stage_value < previous_value:
                self._reset_stage_metrics(stage)
            clamped_value = max(stage_value, 0)
            if self._stage_totals[stage] > 0:
                clamped_value = min(clamped_value, self._stage_totals[stage])
            self._stage_progress[stage] = clamped_value
        stage_value = self._stage_progress.get(stage, 0)
        stage_total = self._stage_totals.get(stage, 0)

        active_stage: str | None = None
        active_value: int = 0
        active_total: int = 0
        for key in self._stage_order:
            total_units = max(int(self._stage_totals.get(key, 0)), 0)
            progress_units = min(max(int(self._stage_progress.get(key, 0)), 0), total_units)
            if total_units > 0 and progress_units < total_units:
                active_stage = key
                active_total = total_units
                active_value = progress_units
                break

        metrics = self._stage_metrics.setdefault(
            stage, {"last_value": 0, "last_timestamp": None, "ema": None}
        )
        runtime = self._stage_runtime.setdefault(
            stage, {"start": None, "completed": False}
        )

        if stage_total > 0 and not runtime.get("completed", False):
            if stage == active_stage and runtime.get("start") is None:
                runtime["start"] = now
                metrics["last_timestamp"] = now

        last_stage_value = int(metrics.get("last_value", 0))
        if stage_value > last_stage_value:
            last_timestamp = metrics.get("last_timestamp")
            if isinstance(last_timestamp, (int, float)):
                delta_time = max(now - float(last_timestamp), 0.0)
                delta_units = stage_value - last_stage_value
                if delta_time > 0 and delta_units > 0:
                    instantaneous = delta_time / delta_units
                    ema = metrics.get("ema")
                    if ema is None:
                        ema = instantaneous
                    elif instantaneous >= ema:
                        ema = (ema * 0.35) + (instantaneous * 0.65)
                    else:
                        ema = (ema * 0.9) + (instantaneous * 0.1)
                    metrics["ema"] = max(ema, 0.0)
            else:
                if runtime.get("start") is None:
                    runtime["start"] = now
            metrics["last_timestamp"] = now
            metrics["last_value"] = stage_value
        elif stage_value == 0 and last_stage_value == 0 and stage == active_stage:
            if runtime.get("start") is None:
                runtime["start"] = now
            metrics["last_timestamp"] = now
        elif stage_value < last_stage_value:
            self._reset_stage_metrics(stage)
            metrics = self._stage_metrics[stage]
            runtime = self._stage_runtime[stage]
            if stage == active_stage and stage_total > 0:
                runtime["start"] = now
                metrics["last_timestamp"] = now
            metrics["last_value"] = stage_value

        if (
            stage_total > 0
            and stage_value >= stage_total
            and not runtime.get("completed", False)
        ):
            start = runtime.get("start")
            if isinstance(start, (int, float)):
                elapsed = max(now - float(start), 0.0)
                self._record_stage_history(stage, elapsed, stage_total)
            runtime["completed"] = True
            runtime["start"] = None
            metrics["last_timestamp"] = now
            metrics["last_value"] = max(stage_total, stage_value)

        stage_label: str = ""
        if active_stage is not None:
            label = _STAGE_LABELS.get(active_stage, active_stage.title())
            if active_total > 0:
                stage_label = f"{label} ({active_value}/{active_total})"
            else:
                stage_label = label
        elif current < total:
            stage_label = "Finalising build..."
        if stage_label != self._progress_stage_text:
            self._progress_stage_text = stage_label
        if active_stage != self._last_logged_stage:
            if active_stage is None:
                if current < total:
                    self.logger.info("Stage complete; finalising remaining tasks…")
                else:
                    self.logger.info("All build stages completed.")
            else:
                if active_total > 0:
                    self.logger.info(
                        "Stage: %s (%s/%s)",
                        _STAGE_LABELS.get(active_stage, active_stage.title()),
                        active_value,
                        active_total,
                    )
                else:
                    self.logger.info(
                        "Stage: %s",
                        _STAGE_LABELS.get(active_stage, active_stage.title()),
                    )
            self._last_logged_stage = active_stage

        percent = int(round(100 * current / total))
        if percent < self._displayed_percent:
            percent = self._displayed_percent
        else:
            self._displayed_percent = percent
        if self.progress_bar.maximum() == 0 and self.progress_bar.minimum() == 0:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(max(0, min(100, percent)))
        self._overall_progress_current = current
        self._overall_progress_total = total
        self._progress_counts_text = f"{current}/{total}"
        self._update_eta_display(now=now)

    def _handle_finished(self, result: BuildResult) -> None:
        self._set_running(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText("Complete")
        if result.exports:
            lines = [f"{fmt.upper()}: {path}" for fmt, path in result.exports.items()]
            export_text = "\n".join(lines)
            open_target = self._preferred_export_path(result.exports)
        else:
            export_text = "No export files were created."
            open_target = None

        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Microwire Data Builder")
        msg.setIcon(QtWidgets.QMessageBox.Icon.Information)
        msg.setText("Build finished successfully.")
        msg.setInformativeText(export_text)
        open_button: QtWidgets.QAbstractButton | None = None
        if open_target is not None:
            open_button = msg.addButton(
                "Open", QtWidgets.QMessageBox.ButtonRole.AcceptRole
            )
        close_button = msg.addButton("Close", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if (
            open_target is not None
            and open_button is not None
            and msg.clickedButton() is open_button
        ):
            self._open_path(open_target)

    def _handle_failed(self, message: str) -> None:
        self._set_running(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Failed")
        QtWidgets.QMessageBox.critical(
            self,
            "Microwire Data Builder",
            "Build failed.\n\n" + message,
        )

    def _handle_cancelled(self) -> None:
        self._set_running(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Cancelled")
        QtWidgets.QMessageBox.information(
            self,
            "Microwire Data Builder",
            "Build cancelled by user.",
        )

    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    # ------------------------------------------------------------------ Qt hooks
    def _append_log(self, level: int, message: str) -> None:
        _ = level
        self.log_view.appendPlainText(message)
        scrollbar = self.log_view.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        if self._running:
            QtWidgets.QMessageBox.warning(
                self,
                "Microwire Data Builder",
                "A build is currently running. Please wait for it to finish before closing.",
            )
            event.ignore()
            return
        if hasattr(self, "_log_handler") and self._log_handler in self.logger.handlers:
            self.logger.removeHandler(self._log_handler)
        self._save_settings()
        super().closeEvent(event)


@dataclass
class SectionProcessResult:
    table: pd.DataFrame
    processed: Dict[str, float]
    payloads: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


class DataFrameModel(QtCore.QAbstractTableModel):
    """Expose a pandas DataFrame to Qt view widgets."""

    def __init__(self, frame: pd.DataFrame | None = None, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._frame = frame.copy() if frame is not None else pd.DataFrame()
        self._decoration_provider: Optional[
            Callable[[pd.Series, str], Optional[QtGui.QPixmap | QtGui.QImage]]
        ] = None

    def set_frame(self, frame: pd.DataFrame | None) -> None:
        self.beginResetModel()
        self._frame = frame.copy() if frame is not None else pd.DataFrame()
        self.endResetModel()

    def set_decoration_provider(
        self,
        provider: Optional[Callable[[pd.Series, str], Optional[QtGui.QPixmap | QtGui.QImage]]],
    ) -> None:
        self._decoration_provider = provider
        try:
            self.layoutChanged.emit()
        except Exception:
            pass

    def frame(self) -> pd.DataFrame:
        return self._frame

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._frame.index)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:  # type: ignore[override]
        if parent.isValid():
            return 0
        return len(self._frame.columns)

    @staticmethod
    def _sort_value(value: Any) -> Any:
        if isinstance(value, (QtGui.QPixmap, QtGui.QImage)):
            return ""
        if isinstance(value, (pd.Timestamp, datetime)):
            return value
        if isinstance(value, (list, tuple, set)):
            return ", ".join(str(item) for item in value)
        return value

    def data(
        self,
        index: QtCore.QModelIndex,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ) -> Any:  # type: ignore[override]
        if not index.isValid():
            return None
        try:
            value = self._frame.iat[index.row(), index.column()]
        except Exception:
            return None
        if role == QtCore.Qt.ItemDataRole.DecorationRole:
            provider = getattr(self, "_decoration_provider", None)
            if provider is not None:
                try:
                    column_label = str(self._frame.columns[index.column()])
                    row_series = self._frame.iloc[index.row()]
                except Exception:
                    pass
                else:
                    try:
                        decoration = provider(row_series, column_label)
                    except Exception:
                        decoration = None
                    if isinstance(decoration, (QtGui.QPixmap, QtGui.QImage)):
                        if isinstance(decoration, QtGui.QImage):
                            return QtGui.QPixmap.fromImage(decoration)
                        return decoration
            if isinstance(value, QtGui.QPixmap):
                return value
            if isinstance(value, QtGui.QImage):
                return QtGui.QPixmap.fromImage(value)
            return None
        if role not in (
            QtCore.Qt.ItemDataRole.DisplayRole,
            QtCore.Qt.ItemDataRole.EditRole,
        ):
            return None
        if isinstance(value, (QtGui.QPixmap, QtGui.QImage)):
            return ""
        if isinstance(value, float):
            if math.isnan(value):
                return ""
            return f"{value:.4g}"
        return str(value) if value is not None else ""

    def headerData(
        self,
        section: int,
        orientation: QtCore.Qt.Orientation,
        role: int = QtCore.Qt.ItemDataRole.DisplayRole,
    ) -> Any:  # type: ignore[override]
        if role != QtCore.Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == QtCore.Qt.Orientation.Horizontal:
            try:
                return str(self._frame.columns[section])
            except Exception:
                return ""
        try:
            label = self._frame.index[section]
        except Exception:
            return str(section + 1)
        return str(label)

    def sort(self, column: int, order: QtCore.Qt.SortOrder = QtCore.Qt.SortOrder.AscendingOrder) -> None:  # type: ignore[override]
        if self._frame.empty:
            return
        try:
            column_label = self._frame.columns[column]
        except Exception:
            return
        ascending = order != QtCore.Qt.SortOrder.DescendingOrder
        try:
            sorted_frame = self._frame.sort_values(
                by=column_label,
                ascending=ascending,
                kind="mergesort",
                key=lambda col: col.map(self._sort_value) if hasattr(col, "map") else col,
            )
        except Exception:
            return
        self.beginResetModel()
        self._frame = sorted_frame.reset_index(drop=True)
        self.endResetModel()


def _dimension_display(field: str, *records: Dict[str, Any]) -> Optional[str]:
    values: List[str] = []
    seen: Set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        bucket = record.get(f"{field}__display")
        if isinstance(bucket, (list, tuple)):
            for entry in bucket:
                text = _clean_str(entry)
                if not text or text in seen:
                    continue
                values.append(text)
                seen.add(text)
        fallback = _value_for_output(record, field)
        if fallback is None:
            continue
        display = _format_dimension_display(field, fallback)
        if not display:
            continue
        if display not in seen:
            values.append(display)
            seen.add(display)
    if not values:
        return None
    return "\n".join(values)


def _fabrication_index_to_frame(index: FabricationIndex) -> pd.DataFrame:
    columns = [
        "Composition",
        "Draw",
        "Piece",
        "Length (m)",
        "Piece date",
        "d (µm)",
        "D (µm)",
        "d/D",
        "Resistance (Ω)",
        "Temperature (°C)",
        "Mass (g)",
        "Winding speed (m/min)",
        "Glass feeding (mm/min)",
        "Underpressure",
        "Notes",
        "Production datetime",
        "_source_paths",
    ]
    rows: List[Dict[str, Any]] = []
    for (composition, draw, piece), piece_record in sorted(index.piece_level.items()):
        draw_record = index.get_draw(composition, draw)
        row: Dict[str, Any] = {column: None for column in columns}
        row["Composition"] = composition
        row["Draw"] = draw
        row["Piece"] = piece
        row["Length (m)"] = _value_for_output(piece_record, "length_m")
        row["Piece date"] = _value_for_output(piece_record, "piece_date")
        row["d (µm)"] = _dimension_display("d_um", piece_record, draw_record)
        row["D (µm)"] = _dimension_display("D_um", piece_record, draw_record)
        row["d/D"] = _dimension_display("d_over_D", piece_record, draw_record)
        piece_resistance = _value_for_output(piece_record, "fabrication_resistance_ohm")
        draw_resistance = _value_for_output(draw_record, "fabrication_resistance_ohm")
        row["Resistance (Ω)"] = piece_resistance if piece_resistance is not None else draw_resistance
        row["Temperature (°C)"] = _value_for_output(draw_record, "fabrication_temperature_c")
        row["Mass (g)"] = _value_for_output(draw_record, "mass_g")
        row["Winding speed (m/min)"] = _value_for_output(draw_record, "winding_speed_m_per_min")
        row["Glass feeding (mm/min)"] = _value_for_output(draw_record, "glass_feed_mm_per_min")
        row["Underpressure"] = _value_for_output(draw_record, "underpressure")
        row["Notes"] = _compose_notes(draw_record, piece_record)
        row["Production datetime"] = _value_for_output(draw_record, "production_datetime")
        sources: List[str] = []
        for record in (piece_record, draw_record):
            path_value = record.get("_source_path") if isinstance(record, dict) else None
            if not path_value:
                continue
            try:
                resolved = str(Path(path_value))
            except Exception:
                resolved = str(path_value)
            if resolved and resolved not in sources:
                sources.append(resolved)
        row["_source_paths"] = sources
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _render_measurement_pixmap(
    record: Optional[MeasurementRecord],
    logger: logging.Logger,
    *,
    width_px: int = ANNEALING_GRAPH_WIDTH,
    height_px: int = ANNEALING_GRAPH_HEIGHT,
) -> Optional[QtGui.QPixmap]:
    if record is None:
        return None
    frame = record.dataframe if isinstance(record.dataframe, pd.DataFrame) else pd.DataFrame()
    if frame.empty:
        return None
    if "I_mA" in frame.columns and "R_ohm" in frame.columns:
        plot_df = pd.DataFrame(
            {
                "I_mA": pd.to_numeric(frame["I_mA"], errors="coerce"),
                "R_Ohm": pd.to_numeric(frame["R_ohm"], errors="coerce"),
            }
        ).dropna()
    elif "I_A" in frame.columns and "R_ohm" in frame.columns:
        plot_df = pd.DataFrame(
            {
                "I_mA": pd.to_numeric(frame["I_A"], errors="coerce") * 1e3,
                "R_Ohm": pd.to_numeric(frame["R_ohm"], errors="coerce"),
            }
        ).dropna()
    elif "I_mA" in frame.columns and "R_Ohm" in frame.columns:
        plot_df = pd.DataFrame(
            {
                "I_mA": pd.to_numeric(frame["I_mA"], errors="coerce"),
                "R_Ohm": pd.to_numeric(frame["R_Ohm"], errors="coerce"),
            }
        ).dropna()
    else:
        columns = [str(column) for column in frame.columns]
        logger.debug(
            "Annealing preview missing expected columns: %s",
            ", ".join(columns),
        )
        return None
    if plot_df.empty:
        return None

    metadata = getattr(record, "metadata", None)
    title = ""
    if metadata is not None:
        try:
            title = format_annealing_title(metadata)
        except Exception:
            title = ""
    figsize = (max(width_px / 96.0, 1.0), max(height_px / 96.0, 1.0))
    canvas_agg: FigureCanvasAgg | None = None
    figure = None
    rc_overrides = {
        "axes.titlesize": ANNEALING_TITLE_FONT_SIZE,
        "axes.labelsize": ANNEALING_AXIS_FONT_SIZE,
        "xtick.labelsize": ANNEALING_TICK_FONT_SIZE,
        "ytick.labelsize": ANNEALING_TICK_FONT_SIZE,
        "lines.linewidth": 1.0,
        "lines.markersize": 3.0,
    }
    try:
        with plt.rc_context(rc_overrides):
            figure, _ = plot_annealing_curve(plot_df, title, figsize=figsize)
        if figure is not None:
            figure.subplots_adjust(left=0.08, right=0.98, top=0.9, bottom=0.16)
            for ax in figure.axes:
                try:
                    ax.tick_params(labelsize=ANNEALING_TICK_FONT_SIZE)
                except Exception:
                    pass
                try:
                    ax.xaxis.label.set_fontsize(ANNEALING_AXIS_FONT_SIZE)
                    ax.yaxis.label.set_fontsize(ANNEALING_AXIS_FONT_SIZE)
                except Exception:
                    pass
                if ax.get_title():
                    try:
                        ax.set_title(ax.get_title(), fontsize=ANNEALING_TITLE_FONT_SIZE)
                    except Exception:
                        pass
                legend = ax.get_legend()
                if legend is not None:
                    try:
                        legend.remove()
                    except Exception:
                        legend.set_visible(False)
        canvas_agg = FigureCanvasAgg(figure)
        canvas_agg.draw()
        width, height = canvas_agg.get_width_height()
        buffer = canvas_agg.buffer_rgba()
        # PyQt6 exposes formats via the QtGui.QImage.Format enum; older builds fall
        # back to module-level constants. Attempt the modern attribute first and
        # gracefully degrade through sensible alternatives so we never raise an
        # AttributeError (which previously prevented any thumbnails from
        # rendering).
        format_candidates: list[QtGui.QImage.Format | int] = []
        for attr in ("Format_RGBA8888", "Format_RGBA8888_Premultiplied", "Format_ARGB32"):
            candidate = None
            try:
                candidate = getattr(QtGui.QImage.Format, attr)
            except AttributeError:
                candidate = getattr(QtGui.QImage, attr, None)
            if candidate is not None:
                format_candidates.append(candidate)
        if not format_candidates:
            try:
                fallback = QtGui.QImage.Format.Format_ARGB32
            except AttributeError:
                fallback = getattr(QtGui.QImage, "Format_ARGB32", None)
            if fallback is None:
                fallback = QtGui.QImage.Format_ARGB32  # type: ignore[attr-defined]
            format_candidates.append(fallback)

        image: QtGui.QImage | None = None
        for fmt in format_candidates:
            try:
                candidate = QtGui.QImage(buffer, width, height, 4 * width, fmt)
            except TypeError:
                continue
            if not candidate.isNull():
                image = candidate
                break
        if image is None:
            try:
                fallback = QtGui.QImage.Format.Format_ARGB32
            except AttributeError:
                fallback = getattr(QtGui.QImage, "Format_ARGB32", None)
            if fallback is None:
                fallback = QtGui.QImage.Format_ARGB32  # type: ignore[attr-defined]
            image = QtGui.QImage(buffer, width, height, 4 * width, fallback)
        return QtGui.QPixmap.fromImage(image.copy())
    except Exception:
        logger.exception(
            "Failed to render annealing preview for %s",
            getattr(record, "path", "<unknown>"),
        )
        return None
    finally:
        if figure is not None:
            plt.close(figure)


def _annealing_records_to_frame(
    records: List[MeasurementRecord],
    logger: logging.Logger,
) -> pd.DataFrame:
    columns = [
        "Composition",
        "Microwire",
        "Graph — 1000 mA",
        "Graph — low mA",
        "Low current setpoint",
        "Updated",
        "_group_key",
        "_sources",
    ]
    grouped: Dict[Tuple[str, int, int], List[MeasurementRecord]] = {}
    for record in records:
        metadata = getattr(record, "metadata", None)
        if metadata is None:
            continue
        composition = getattr(metadata, "composition_token", None)
        draw = getattr(metadata, "draw_x", None)
        piece = getattr(metadata, "piece_y", None)
        if composition is None or draw is None or piece is None:
            continue
        try:
            key = (str(composition), int(draw), int(piece))
        except (TypeError, ValueError):
            continue
        grouped.setdefault(key, []).append(record)

    rows: List[Dict[str, Any]] = []
    for (composition, draw, piece), group in sorted(grouped.items()):
        high_record, low_record = _select_high_low_pair(group)
        try:
            microwire = _microwire_label(draw, piece)
        except Exception:
            microwire = f"{draw}/{piece}"

        def _mtime(entry: MeasurementRecord) -> Optional[float]:
            path = getattr(entry, "path", None)
            if not path:
                return None
            try:
                return Path(path).stat().st_mtime
            except Exception:
                return None

        timestamps = [value for record in group if (value := _mtime(record)) is not None]
        updated = (
            datetime.fromtimestamp(max(timestamps)).isoformat(timespec="seconds")
            if timestamps
            else ""
        )

        low_setpoint = _format_setpoint(_extract_setpoint(low_record))

        group_key = f"{composition}|{draw}|{piece}"
        source_paths: List[str] = []
        for entry in (high_record, low_record):
            path = getattr(entry, "path", None)
            if path:
                source_paths.append(str(Path(path)))
        if source_paths:
            source_paths = list(dict.fromkeys(source_paths))
        rows.append(
            {
                "Composition": composition,
                "Microwire": microwire,
                "Graph — 1000 mA": None,
                "Graph — low mA": None,
                "Low current setpoint": low_setpoint,
                "Updated": updated,
                "_group_key": group_key,
                "_sources": source_paths,
            }
        )

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _extract_setpoint(record: Optional[MeasurementRecord]) -> Optional[float]:
    if record is None:
        return None
    value = getattr(getattr(record, "metadata", object()), "setpoint_mA", None)
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _format_setpoint(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return ""
    rounded = int(round(value))
    if abs(value - rounded) < 1e-6:
        return f"{rounded}"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _select_high_low_pair(
    records: List[MeasurementRecord],
) -> Tuple[Optional[MeasurementRecord], Optional[MeasurementRecord]]:
    high_record = _select_high_measurement(records)
    low_record = _select_low_measurement(records)
    if (
        high_record
        and low_record
        and _extract_setpoint(high_record) == _extract_setpoint(low_record)
    ):
        setpoints = {
            _extract_setpoint(record)
            for record in records
            if _extract_setpoint(record) is not None
        }
        if len(setpoints) <= 1:
            low_record = None
    return high_record, low_record


class _AnnealingPlotDisplay(QtWidgets.QWidget):
    """Render a single annealing plot with contextual details."""

    def __init__(
        self,
        title: str,
        logger: logging.Logger,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._base_title = title
        self._logger = logger
        self._canvas: FigureCanvasQTAgg | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.title_label)

        self.subtitle_label = QtWidgets.QLabel("")
        self.subtitle_label.setObjectName("annealingPlotSubtitle")
        self.subtitle_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.subtitle_label.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self.subtitle_label)

        self._stack = QtWidgets.QStackedLayout()
        self._stack.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._stack, 1)

        self._placeholder = QtWidgets.QLabel(
            "Select a row to preview the annealing measurement."
        )
        self._placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._stack.addWidget(self._placeholder)

    def set_record(
        self,
        record: Optional[MeasurementRecord],
        *,
        setpoint: Optional[float],
        description: str,
    ) -> None:
        if record is None:
            self._show_placeholder(description)
            self.title_label.setText(self._base_title)
            return

        try:
            figure = self._build_figure(record)
        except Exception:
            self._logger.exception("Failed to render annealing preview for %s", getattr(record, "path", "?"))
            self._show_placeholder("Failed to render plot for the selected measurement.")
            self.title_label.setText(self._base_title)
            return

        display_title = self._base_title
        formatted_setpoint = _format_setpoint(setpoint)
        if formatted_setpoint:
            display_title = f"{self._base_title} ({formatted_setpoint} mA)"
        self.title_label.setText(display_title)

        details: List[str] = []
        if formatted_setpoint:
            details.append(f"{formatted_setpoint} mA")
        try:
            sample_count = len(record.dataframe.index)
        except Exception:
            sample_count = None
        if sample_count is not None:
            details.append(f"{sample_count} sample(s)")
        file_name = getattr(getattr(record, "metadata", object()), "file_name", "")
        if not file_name:
            path = getattr(record, "path", None)
            if path:
                try:
                    file_name = Path(path).name
                except Exception:
                    file_name = ""
        if file_name:
            details.append(str(file_name))
        self.subtitle_label.setText(" · ".join(details))

        canvas = FigureCanvasQTAgg(figure)
        if self._canvas is not None:
            self._stack.removeWidget(self._canvas)
            self._canvas.setParent(None)
            self._canvas.deleteLater()
        self._stack.insertWidget(0, canvas)
        self._stack.setCurrentWidget(canvas)
        self._canvas = canvas

    def clear(self, message: str) -> None:
        self._show_placeholder(message)
        self.title_label.setText(self._base_title)

    def _show_placeholder(self, message: str) -> None:
        if self._canvas is not None:
            self._stack.removeWidget(self._canvas)
            self._canvas.setParent(None)
            self._canvas.deleteLater()
            self._canvas = None
        self.subtitle_label.setText("")
        self._placeholder.setText(message)
        self._stack.setCurrentWidget(self._placeholder)

    def _build_figure(self, record: MeasurementRecord):
        frame = record.dataframe if isinstance(record.dataframe, pd.DataFrame) else pd.DataFrame()
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise ValueError("No data to plot")
        if "I_mA" in frame.columns and "R_ohm" in frame.columns:
            plot_df = pd.DataFrame(
                {
                    "I_mA": pd.to_numeric(frame["I_mA"], errors="coerce"),
                    "R_Ohm": pd.to_numeric(frame["R_ohm"], errors="coerce"),
                }
            ).dropna()
        elif "I_A" in frame.columns and "R_ohm" in frame.columns:
            plot_df = pd.DataFrame(
                {
                    "I_mA": pd.to_numeric(frame["I_A"], errors="coerce") * 1e3,
                    "R_Ohm": pd.to_numeric(frame["R_ohm"], errors="coerce"),
                }
            ).dropna()
        elif "I_mA" in frame.columns and "R_Ohm" in frame.columns:
            plot_df = pd.DataFrame(
                {
                    "I_mA": pd.to_numeric(frame["I_mA"], errors="coerce"),
                    "R_Ohm": pd.to_numeric(frame["R_Ohm"], errors="coerce"),
                }
            ).dropna()
        else:
            raise ValueError("Current annealing dataframe missing expected columns")
        if plot_df.empty:
            raise ValueError("No valid samples to plot")
        path = getattr(record, "path", None)
        stem = None
        if path:
            try:
                stem = Path(path).stem
            except Exception:
                stem = None
        if not stem:
            stem = getattr(getattr(record, "metadata", object()), "file_name", "")
        title = format_annealing_title(str(stem or "Current annealing"))
        figure, _ = plot_annealing_curve(plot_df, title, figsize=DEFAULT_FIGSIZE)
        return figure


class AnnealingPlotPanel(QtWidgets.QWidget):
    """Display paired annealing plots for the selected microwire."""

    def __init__(
        self,
        logger: logging.Logger,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.header_label = QtWidgets.QLabel("Select a row to preview annealing plots.")
        self.header_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.header_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, self)
        self._high_display = _AnnealingPlotDisplay("Graph — 1000 mA", logger, splitter)
        self._low_display = _AnnealingPlotDisplay("Graph — low mA", logger, splitter)
        splitter.addWidget(self._high_display)
        splitter.addWidget(self._low_display)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

    def update_selection(
        self,
        key: Optional[Tuple[str, int, int]],
        high: Optional[MeasurementRecord],
        low: Optional[MeasurementRecord],
    ) -> None:
        if key is None:
            self.header_label.setText("Select a row to preview annealing plots.")
            self._high_display.clear("Select a row to view the 1000 mA measurement.")
            self._low_display.clear("Select a row to view the low-current measurement.")
            return

        composition, draw, piece = key
        try:
            microwire = _microwire_label(draw, piece)
        except Exception:
            microwire = f"{draw}/{piece}"
        self.header_label.setText(f"{composition} — {microwire}")

        self._high_display.set_record(
            high,
            setpoint=_extract_setpoint(high),
            description="No 1000 mA measurement available for this microwire.",
        )
        self._low_display.set_record(
            low,
            setpoint=_extract_setpoint(low),
            description="No lower-current measurement available for this microwire.",
        )

def _microscope_index_to_frame(
    index: Dict[Tuple[str, int, int], MicroscopeMeasurements],
    overrides: Dict[str, Dict[str, float]],
) -> pd.DataFrame:
    columns = [
        "Composition",
        "Draw",
        "Piece",
        "d (µm)",
        "D (µm)",
        "d/D",
        "Images",
        "_key",
        "_images",
    ]
    rows: List[Dict[str, Any]] = []
    for (composition, draw, piece), measurements in sorted(index.items()):
        key = f"{composition}|{draw}|{piece}"
        override = overrides.get(key, {})
        d_value = override.get("d")
        if d_value is None:
            d_value = measurements.best_core()
        D_value = override.get("D")
        if D_value is None:
            D_value = measurements.best_glass()
        ratio = None
        if isinstance(d_value, (int, float)) and isinstance(D_value, (int, float)) and D_value:
            try:
                ratio = float(d_value) / float(D_value)
            except ZeroDivisionError:
                ratio = None
        if isinstance(ratio, (int, float)):
            ratio = round(float(ratio), 3)
        image_paths: List[str] = []
        for bucket in (measurements.core, measurements.glass, measurements.other):
            for detection in bucket:
                path = getattr(detection, "image_path", None)
                if path:
                    image_paths.append(str(path))
        if image_paths:
            image_paths = list(dict.fromkeys(image_paths))
        rows.append(
            {
                "Composition": composition,
                "Draw": draw,
                "Piece": piece,
                "d (µm)": d_value,
                "D (µm)": D_value,
                "d/D": ratio,
                "Images": "; ".join(dict.fromkeys(image_paths)),
                "_key": key,
                "_images": image_paths,
            }
        )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _video_index_to_frame(
    index: Dict[Tuple[str, int, Optional[int]], VideoMetricsSummary]
) -> pd.DataFrame:
    columns = [
        "Composition",
        "Draw",
        "Piece",
        "Temperature (°C)",
        "Underpressure",
        "Winding speed (m/min)",
        "Glass feeding (mm/min)",
        "_sources",
    ]
    rows: List[Dict[str, Any]] = []
    for (composition, draw, piece), summary in sorted(index.items()):
        rows.append(
            {
                "Composition": composition,
                "Draw": draw,
                "Piece": piece,
                "Temperature (°C)": summary.temperature(),
                "Underpressure": summary.underpressure(),
                "Winding speed (m/min)": summary.winding_speed(),
                "Glass feeding (mm/min)": summary.glass_feed(),
                "_sources": sorted(str(path) for path in getattr(summary, "sources", set())),
            }
        )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _strain_records_to_frame(records: Dict[Tuple[str, int, int], StrainRecord]) -> pd.DataFrame:
    columns = [
        "Composition",
        "Draw",
        "Piece",
        "Microwire",
        "Strain (%)",
        "Broke",
    ]
    rows: List[Dict[str, Any]] = []
    for (composition, draw, piece), record in sorted(records.items()):
        rows.append(
            {
                "Composition": composition,
                "Draw": draw,
                "Piece": piece,
                "Microwire": record.microwire_label,
                "Strain (%)": record.percent,
                "Broke": bool(record.broke),
            }
        )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _apply_microscope_overrides(
    index: Dict[Tuple[str, int, int], MicroscopeMeasurements],
    overrides: Dict[str, Dict[str, float]],
) -> Dict[Tuple[str, int, int], MicroscopeMeasurements]:
    result: Dict[Tuple[str, int, int], MicroscopeMeasurements] = {}
    for key, measurements in index.items():
        clone = MicroscopeMeasurements(
            core=list(measurements.core),
            glass=list(measurements.glass),
            other=list(measurements.other),
        )
        token = f"{key[0]}|{key[1]}|{key[2]}"
        override = overrides.get(token, {})
        d_value = override.get("d")
        if isinstance(d_value, (int, float)) and d_value > 0:
            detection = MicroscopeDetection(
                value=float(d_value),
                image_path=None,
                source="manual",
            )
            detection.category = "core"
            clone.core.insert(0, detection)
        D_value = override.get("D")
        if isinstance(D_value, (int, float)) and D_value > 0:
            detection = MicroscopeDetection(
                value=float(D_value),
                image_path=None,
                source="manual",
            )
            detection.category = "glass"
            clone.glass.insert(0, detection)
        result[key] = clone
    return result



class MiniDatabaseSection(QtWidgets.QWidget):
    """Base widget for mini-database sections that process a subset of data."""

    section_key = "base"
    section_title = "Base"
    supported_suffixes: tuple[str, ...] = ()
    recursive_search = True

    status_changed = QtCore.pyqtSignal(str)
    sources_changed = QtCore.pyqtSignal(list)
    _processing_owner: ClassVar[Optional["MiniDatabaseSection"]] = None
    _refresh_queue: ClassVar[List["MiniDatabaseSection"]] = []
    _SCROLL_SINGLE_STEP = 12

    def __init__(
        self,
        logger: logging.Logger,
        log_callback: Callable[[int, str], None],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.logger = logger
        self._log_callback = log_callback
        self.store = MiniDatabaseStore(self.section_key)
        self.data = self.store.load()
        self.model = DataFrameModel(self.data.table)
        self.table_view: QtWidgets.QTableView | None = None
        self._cancel_requested = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.main_layout = layout

        controls = QtWidgets.QHBoxLayout()
        self.source_button = QtWidgets.QPushButton()
        self.source_button.setText("Connect folder…")
        self.source_button.clicked.connect(self._toggle_source)
        controls.addWidget(self.source_button)

        self.open_sources_button = QtWidgets.QPushButton("Open source file(s)")
        self.open_sources_button.setEnabled(False)
        self.open_sources_button.clicked.connect(self._open_selected_sources)
        controls.addWidget(self.open_sources_button)

        controls.addStretch(1)

        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._request_cancel)
        controls.addWidget(self.stop_button)

        layout.addLayout(controls)
        self.controls_layout = controls

        self.status_label = QtWidgets.QLabel()
        layout.addWidget(self.status_label)

        progress_row = QtWidgets.QHBoxLayout()
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_row.addWidget(self.progress_bar, 1)
        self.progress_label = QtWidgets.QLabel("Idle")
        progress_row.addWidget(self.progress_label)
        self.progress_eta_label = QtWidgets.QLabel("")
        self.progress_eta_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        progress_row.addWidget(self.progress_eta_label)
        layout.addLayout(progress_row)
        self._progress_total: int = 0
        self._progress_current: int = 0
        self._progress_start: float | None = None

        self.sources_list = QtWidgets.QListWidget(self)
        self.sources_list.hide()

        right_panel = self.create_right_panel(self)
        layout.addWidget(right_panel, 1)
        self._configure_table_view()

        self._populate_sources_list()
        self.model.set_frame(self.data.table)
        self._auto_fit_columns()
        self._update_status()
        self._reset_progress_ui()
        self._hook_table_selection()
        self._update_open_sources_enabled()

    # ------------------------------------------------------------------ UI helpers
    def create_right_panel(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        table = QtWidgets.QTableView(parent)
        table.setModel(self.model)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        table.setSortingEnabled(True)
        self.table_view = table
        container = QtWidgets.QWidget(parent)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(table, 1)
        return container

    def _configure_table_view(self) -> None:
        table = self.table_view
        if not isinstance(table, QtWidgets.QTableView):
            return
        try:
            table.setIconSize(
                QtCore.QSize(ANNEALING_GRAPH_WIDTH, ANNEALING_GRAPH_HEIGHT)
            )
        except Exception:
            pass
        table.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        table.setHorizontalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        vertical_bar = table.verticalScrollBar()
        if vertical_bar is not None:
            vertical_bar.setSingleStep(self._SCROLL_SINGLE_STEP)

    def _auto_fit_columns(self) -> None:
        table = self.table_view
        if not isinstance(table, QtWidgets.QTableView):
            return
        try:
            table.resizeColumnsToContents()
        except Exception:
            return
        model = getattr(table, "model", lambda: None)()
        frame: Optional[pd.DataFrame] = None
        if hasattr(model, "frame"):
            try:
                frame = model.frame()
            except Exception:
                frame = None
        if frame is None or getattr(frame, "empty", False):
            return
        try:
            icon_width = table.iconSize().width()
        except Exception:
            icon_width = 0
        if icon_width <= 0:
            icon_width = ANNEALING_GRAPH_WIDTH
        graph_width = max(int(icon_width), ANNEALING_GRAPH_WIDTH) + 80
        for idx, column_name in enumerate(frame.columns):
            label = str(column_name)
            if "Graph" not in label and "Figure" not in label:
                continue
            current = table.columnWidth(idx)
            target = graph_width if graph_width > current else current
            if target > 0:
                table.setColumnWidth(idx, target)

    def _update_source_button(self) -> None:
        has_sources = self.sources_list.count() > 0
        text = "Remove folder…" if has_sources else "Connect folder…"
        self.source_button.setText(text)
        if has_sources:
            self.source_button.setToolTip("Disconnect the currently linked folder.")
        else:
            self.source_button.setToolTip("Select a folder to analyse.")

    def _populate_sources_list(self) -> None:
        self.sources_list.clear()
        for source in self.data.sources:
            self.sources_list.addItem(source)
        self._update_source_button()

    def _reset_progress_ui(self) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Idle")
        self.progress_eta_label.clear()
        self._progress_total = 0
        self._progress_current = 0
        self._progress_start = None
        self._cancel_requested = False
        self.stop_button.setEnabled(False)

    def _hide_columns(self, names: Sequence[str]) -> None:
        if not isinstance(self.table_view, QtWidgets.QTableView):
            return
        model = getattr(self.table_view, "model", lambda: None)()
        frame: Optional[pd.DataFrame] = None
        if hasattr(model, "frame"):
            try:
                frame = model.frame()
            except Exception:
                frame = None
        if frame is None:
            return
        columns = list(frame.columns)
        for name in names:
            if name not in columns:
                continue
            index = columns.index(name)
            try:
                self.table_view.setColumnHidden(index, True)
            except Exception:
                continue

    def _hook_table_selection(self) -> None:
        if not isinstance(self.table_view, QtWidgets.QTableView):
            return
        selection_model = self.table_view.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(
                lambda *_: self._update_open_sources_enabled()
            )
        model = self.table_view.model()
        if isinstance(model, QtCore.QAbstractItemModel):
            model.modelReset.connect(self._update_open_sources_enabled)
            model.dataChanged.connect(lambda *_: self._update_open_sources_enabled())

    def _selected_rows(self) -> List[int]:
        if not isinstance(self.table_view, QtWidgets.QTableView):
            return []
        selection = self.table_view.selectionModel()
        if selection is None:
            return []
        rows = {index.row() for index in selection.selectedRows()}
        return sorted(rows)

    def _row_series(self, row: int) -> Optional[pd.Series]:
        frame = self.model.frame()
        if row < 0 or row >= len(frame.index):
            return None
        try:
            return frame.iloc[row]
        except Exception:
            return None

    def _row_sources(self, row: pd.Series) -> List[Path]:
        _ = row
        return []

    def _update_open_sources_enabled(self) -> None:
        if not hasattr(self, "open_sources_button"):
            return
        rows = self._selected_rows()
        enabled = False
        if rows:
            for row_index in rows:
                series = self._row_series(row_index)
                if series is None:
                    continue
                if self._row_sources(series):
                    enabled = True
                    break
        self.open_sources_button.setEnabled(enabled)

    def _open_file(self, path: Path) -> bool:
        resolved = path.expanduser()
        if not resolved.exists():
            self.log(
                f"{self.section_title}: source file missing — {resolved}",
                level=logging.WARNING,
            )
            return False
        url = QtCore.QUrl.fromLocalFile(str(resolved))
        opened = QtGui.QDesktopServices.openUrl(url)
        if not opened:
            self.log(
                f"{self.section_title}: could not open {resolved}",
                level=logging.WARNING,
            )
        return opened

    def _open_selected_sources(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QtWidgets.QMessageBox.information(
                self,
                self.section_title,
                "Select one or more rows to open their source files.",
            )
            return
        opened_any = False
        missing: List[Path] = []
        seen: set[Path] = set()
        for row_index in rows:
            series = self._row_series(row_index)
            if series is None:
                continue
            for path in self._row_sources(series):
                if path in seen:
                    continue
                seen.add(path)
                if self._open_file(path):
                    opened_any = True
                else:
                    missing.append(path)
        if not opened_any and missing:
            details = "\n".join(str(p) for p in missing[:5])
            if len(missing) > 5:
                details += "\n…"
            QtWidgets.QMessageBox.warning(
                self,
                self.section_title,
                f"None of the selected rows have available source files.\n\n{details}",
            )
    def _start_progress(self, total: int) -> None:
        self._progress_total = max(int(total), 0)
        self._progress_current = 0
        self._progress_start = time.monotonic()
        if self._progress_total <= 0:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setValue(0)
            self.progress_label.setText("Scanning…")
            self.progress_eta_label.clear()
            return
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"Processing 0/{self._progress_total}")
        self.progress_eta_label.setText("Estimating…")

    def _update_progress(self, current: int, total: Optional[int], message: Optional[str]) -> None:
        if total is not None:
            self._progress_total = max(int(total), 0)
        self._progress_current = max(int(current), 0)
        total_units = self._progress_total
        if total_units <= 0:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            percent = int(round(min(self._progress_current / total_units, 1.0) * 100)) if total_units else 0
            self.progress_bar.setValue(max(0, min(100, percent)))
        parts: list[str] = []
        if message:
            parts.append(message)
        if total_units > 0:
            parts.append(f"{self._progress_current}/{total_units}")
        else:
            parts.append(f"{self._progress_current} processed")
        self.progress_label.setText(" — ".join(parts))
        start = self._progress_start
        eta_text = ""
        if (
            start is not None
            and self._progress_current > 0
            and total_units > 0
            and self._progress_current < total_units
        ):
            elapsed = time.monotonic() - start
            rate = elapsed / self._progress_current if self._progress_current else None
            if rate and math.isfinite(rate):
                eta = rate * (total_units - self._progress_current)
                eta_text = f"ETA {_format_duration(eta)}"
        elif start is not None and self._progress_current and (
            total_units == 0 or self._progress_current >= total_units
        ):
            elapsed = time.monotonic() - start
            eta_text = f"Elapsed {_format_duration(elapsed)}"
        self.progress_eta_label.setText(eta_text)
        QtWidgets.QApplication.processEvents(
            QtCore.QEventLoop.ProcessEventsFlag.AllEvents
        )

    def _request_cancel(self) -> None:
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self.stop_button.setEnabled(False)
        self.progress_label.setText("Cancelling…")
        self.progress_bar.setRange(0, 0)
        QtWidgets.QApplication.processEvents(
            QtCore.QEventLoop.ProcessEventsFlag.AllEvents
        )

    def is_cancelled(self) -> bool:
        return self._cancel_requested

    def _check_cancelled(self) -> None:
        if self._cancel_requested:
            raise BuildCancelledError()

    def _finish_progress(self) -> None:
        if self._progress_total > 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
            self.progress_label.setText(
                f"Complete — {self._progress_total} file(s)"
            )
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100)
            self.progress_label.setText("Complete")
        start = self._progress_start
        if start is not None:
            elapsed = time.monotonic() - start
            self.progress_eta_label.setText(f"Elapsed {_format_duration(elapsed)}")
        else:
            self.progress_eta_label.clear()
        self._progress_start = None
        self._cancel_requested = False
        self.stop_button.setEnabled(False)
        self._release_processing()

    def _fail_progress(self) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Failed")
        self.progress_eta_label.clear()
        self._progress_start = None
        self._cancel_requested = False
        self.stop_button.setEnabled(False)
        self._release_processing()

    def _cancel_progress(self) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Cancelled")
        self.progress_eta_label.clear()
        self._progress_start = None
        self._cancel_requested = False
        self.stop_button.setEnabled(False)
        self._release_processing()

    def _release_processing(self) -> None:
        if MiniDatabaseSection._processing_owner is self:
            MiniDatabaseSection._processing_owner = None
            if MiniDatabaseSection._refresh_queue:
                next_section = MiniDatabaseSection._refresh_queue.pop(0)
                if isinstance(next_section, MiniDatabaseSection):
                    QtCore.QTimer.singleShot(0, next_section.refresh)

    def _progress_callback(
        self, current: int, total: int, message: Optional[str] = None
    ) -> None:
        if self._progress_start is None:
            self._start_progress(total)
        self._update_progress(current, total, message)

    def _sync_sources(self) -> None:
        sources: list[str] = []
        for index in range(self.sources_list.count()):
            item = self.sources_list.item(index)
            if item is not None:
                sources.append(item.text())
        self.data.sources = sources
        self.store.save(self.data)
        try:
            self.sources_changed.emit(list(sources))
        except Exception:
            pass
        self._update_status()
        self._update_source_button()

    def set_sources(self, sources: Iterable[str]) -> None:
        unique = []
        seen: Set[str] = set()
        for source in sources:
            normalised = str(Path(source).expanduser())
            if normalised not in seen:
                seen.add(normalised)
                unique.append(normalised)
        self.sources_list.clear()
        for path in unique:
            self.sources_list.addItem(path)
        self._sync_sources()

    def _toggle_source(self) -> None:
        if self.sources_list.count() == 0:
            self._add_source()
        else:
            self._prompt_remove_source()

    def _add_source(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, self.section_title)
        if not directory:
            return
        normalised = str(Path(directory).expanduser())
        existing = {self.sources_list.item(idx).text() for idx in range(self.sources_list.count())}
        if normalised not in existing:
            self.sources_list.addItem(normalised)
            self._sync_sources()

    def _prompt_remove_source(self) -> None:
        sources = [self.sources_list.item(idx).text() for idx in range(self.sources_list.count())]
        if not sources:
            return
        target = sources[0]
        if len(sources) > 1:
            selection, ok = QtWidgets.QInputDialog.getItem(
                self,
                self.section_title,
                "Select a folder to disconnect:",
                sources,
                0,
                False,
            )
            if not ok or not selection:
                return
            target = selection
        message = QtWidgets.QMessageBox(self)
        message.setWindowTitle(self.section_title)
        message.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        message.setText("Remove the connected folder?")
        message.setInformativeText(target)
        remove_btn = message.addButton("Remove folder", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        message.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        message.exec()
        if message.clickedButton() is not remove_btn:
            return
        for idx in range(self.sources_list.count() - 1, -1, -1):
            item = self.sources_list.item(idx)
            if item is not None and item.text() == target:
                self.sources_list.takeItem(idx)
        self._sync_sources()

    # ------------------------------------------------------------------ data handling
    def _collect_candidates(self) -> List[Path]:
        candidates: Dict[str, Path] = {}
        for source in self.data.sources:
            root = Path(source).expanduser()
            if not root.exists():
                continue
            iterator: Iterable[Path]
            try:
                iterator = root.rglob("*") if self.recursive_search else root.glob("*")
            except Exception:
                continue
            for path in iterator:
                if not path.is_file():
                    continue
                if self.supported_suffixes and path.suffix.lower() not in self.supported_suffixes:
                    continue
                try:
                    resolved = str(path.resolve())
                except Exception:
                    resolved = str(path)
                candidates.setdefault(resolved, path)
        return sorted(candidates.values())

    def _pending_paths(self) -> List[Path]:
        pending: List[Path] = []
        processed = self.data.processed
        for path in self._collect_candidates():
            key = str(path)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if float(processed.get(key, -1.0)) != float(mtime):
                pending.append(path)
        return pending

    def _update_status(self) -> None:
        sources_count = len(self.data.sources)
        pending = self._pending_paths() if sources_count else []
        if sources_count == 0:
            message = "Connect one or more folders to begin."
            self.refresh_button.setEnabled(False)
        else:
            self.refresh_button.setEnabled(True)
            if pending:
                message = f"⚠️ {len(pending)} new or updated file(s) pending processing."
            elif not self.data.table.empty:
                message = f"Up to date ({len(self.data.table)} record(s))."
            else:
                message = "No processed data available yet."
        self.status_label.setText(message)
        try:
            self.status_changed.emit(message)
        except Exception:
            pass

    def log(self, message: str, level: int = logging.INFO) -> None:
        try:
            self._log_callback(level, message)
        except Exception:
            self.logger.log(level, message)

    def refresh(self) -> None:
        candidates = self._collect_candidates()
        if not candidates:
            self.log(f"{self.section_title}: no files found in connected folders.")
            self.data.processed = {}
            self.data.table = pd.DataFrame()
            self.store.save(self.data)
            self.model.set_frame(self.data.table)
            self._auto_fit_columns()
            self._update_status()
            self._reset_progress_ui()
            return
        self._cancel_requested = False
        self.stop_button.setEnabled(True)
        owner = MiniDatabaseSection._processing_owner
        if owner is not None and owner is not self:
            other_title = getattr(owner, "section_title", "Another section")
            if self not in MiniDatabaseSection._refresh_queue:
                MiniDatabaseSection._refresh_queue.append(self)
            status_message = f"Queued — waiting for {other_title}"
            self.status_label.setText(status_message)
            try:
                self.status_changed.emit(status_message)
            except Exception:
                pass
            self.log(
                f"{self.section_title}: queued refresh while {other_title} completes."
            )
            self._reset_progress_ui()
            self.stop_button.setEnabled(False)
            return
        MiniDatabaseSection._processing_owner = self
        status_message = f"Processing {len(candidates)} file(s)…"
        self.status_label.setText(status_message)
        try:
            self.status_changed.emit(status_message)
        except Exception:
            pass
        self._start_progress(len(candidates))
        try:
            result = self.process(candidates, progress=self._progress_callback)
        except BuildCancelledError:
            self.log(f"{self.section_title}: processing cancelled by user.")
            self._cancel_progress()
            return
        except Exception as exc:  # pragma: no cover - defensive UI guard
            self.logger.exception("%s processing failed", self.section_title)
            self._fail_progress()
            QtWidgets.QMessageBox.critical(
                self,
                self.section_title,
                f"Failed to process data:\n{exc}",
            )
            return
        self._finish_progress()

        existing_payloads = set(self.data.extra.get("payloads", {}).keys())
        new_payloads = set(result.payloads.keys())
        for name in existing_payloads - new_payloads:
            self.store.clear_payload(name)

        payload_map: Dict[str, str] = {}
        for name, payload in result.payloads.items():
            self.store.save_payload(name, payload)
            payload_map[name] = name
        if payload_map:
            self.data.extra["payloads"] = payload_map
        if result.extra:
            self.data.extra.update(result.extra)
        self.data.processed = result.processed
        self.data.table = result.table
        self.store.save(self.data)
        self.model.set_frame(result.table)
        self._auto_fit_columns()
        self._update_status()
        self.log(
            f"{self.section_title}: processed {len(candidates)} file(s)."
        )
        self._update_open_sources_enabled()
        try:
            self.sources_changed.emit(list(self.data.sources))
        except Exception:
            pass

    # ------------------------------------------------------------------ hooks for subclasses
    def process(
        self,
        paths: List[Path],
        progress: Optional[Callable[[int, int, Optional[str]], None]] = None,
    ) -> SectionProcessResult:
        raise NotImplementedError

class FabricationSection(MiniDatabaseSection):
    section_key = "fabrication"
    section_title = "Fabrication data"
    supported_suffixes = (".xlsx", ".xls", ".xlsm")

    def __init__(
        self,
        logger: logging.Logger,
        log_callback: Callable[[int, str], None],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(logger, log_callback, parent)
        self._hide_columns(["_source_paths", "_source_path"])

    def _collect_candidates(self) -> List[Path]:
        _, relevant_compositions = self._load_relevant_map()
        tokens = {
            self._normalise_token(comp)
            for comp in relevant_compositions
            if self._normalise_token(comp)
        }
        if not tokens:
            return super()._collect_candidates()

        candidates: Dict[str, Path] = {}

        def _should_consider(path: Path, root: Path) -> bool:
            try:
                relative = path.relative_to(root)
                text = self._normalise_token(str(relative))
            except Exception:
                text = self._normalise_token(path.name)
            if not text:
                return False
            return any(token in text for token in tokens)

        for source in self.data.sources:
            root = Path(source).expanduser()
            if not root.exists():
                continue
            try:
                resolved_root = root.resolve()
            except Exception:
                resolved_root = root
            if self.recursive_search:
                stack: List[Tuple[Path, bool]] = [(resolved_root, False)]
                while stack:
                    current, matched = stack.pop()
                    try:
                        entries = list(current.iterdir())
                    except Exception:
                        continue
                    for entry in entries:
                        if entry.is_dir():
                            next_matched = matched or _should_consider(entry, resolved_root)
                            if current is resolved_root or next_matched:
                                stack.append((entry, next_matched))
                            continue
                        if not entry.is_file():
                            continue
                        if self.supported_suffixes and entry.suffix.lower() not in self.supported_suffixes:
                            continue
                        if not (matched or _should_consider(entry, resolved_root)):
                            continue
                        try:
                            resolved = str(entry.resolve())
                        except Exception:
                            resolved = str(entry)
                        candidates.setdefault(resolved, entry)
            else:
                try:
                    entries = list(resolved_root.iterdir())
                except Exception:
                    entries = []
                for entry in entries:
                    if not entry.is_file():
                        continue
                    if self.supported_suffixes and entry.suffix.lower() not in self.supported_suffixes:
                        continue
                    if not _should_consider(entry, resolved_root):
                        continue
                    try:
                        resolved = str(entry.resolve())
                    except Exception:
                        resolved = str(entry)
                    candidates.setdefault(resolved, entry)
        if not candidates:
            return super()._collect_candidates()
        return sorted(candidates.values())

    @staticmethod
    def _normalise_int(value: object) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, (int,)):
            return int(value)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(numeric):
            return None
        return int(numeric)

    def _load_relevant_map(
        self,
    ) -> Tuple[Dict[str, Dict[Optional[int], Set[Optional[int]]]], Set[str]]:
        try:
            store = MiniDatabaseStore("annealing")
            records = store.load_payload("annealing_records")
        except Exception:
            records = None
        relevant: Dict[str, Dict[Optional[int], Set[Optional[int]]]] = {}
        if isinstance(records, list):
            for record in records:
                metadata = getattr(record, "metadata", None)
                if metadata is None:
                    continue
                composition = getattr(metadata, "composition_token", None)
                if not composition:
                    continue
                composition_key = str(composition).strip()
                if not composition_key:
                    continue
                draw_value = self._normalise_int(getattr(metadata, "draw_x", None))
                piece_value = self._normalise_int(getattr(metadata, "piece_y", None))
                bucket = relevant.setdefault(composition_key, {})
                piece_bucket = bucket.setdefault(draw_value, set())
                piece_bucket.add(piece_value)
        return relevant, set(relevant.keys())

    @staticmethod
    def _allow_draw(
        relevant_map: Dict[str, Dict[Optional[int], Set[Optional[int]]]],
        composition: str,
        draw: int,
    ) -> bool:
        draw_map = relevant_map.get(composition)
        if not draw_map:
            return True
        if draw in draw_map:
            return True
        if None in draw_map:
            return True
        return False

    @staticmethod
    def _allow_piece(
        relevant_map: Dict[str, Dict[Optional[int], Set[Optional[int]]]],
        composition: str,
        draw: int,
        piece: int,
    ) -> bool:
        draw_map = relevant_map.get(composition)
        if not draw_map:
            return True
        allowed: Set[Optional[int]] = set()
        direct = draw_map.get(draw)
        if direct:
            allowed.update(direct)
        fallback = draw_map.get(None)
        if fallback:
            allowed.update(fallback)
        if not allowed:
            return False
        if None in allowed:
            return True
        return piece in allowed

    def _filter_index(
        self,
        index: FabricationIndex,
        relevant_map: Dict[str, Dict[Optional[int], Set[Optional[int]]]],
        relevant_compositions: Set[str],
    ) -> FabricationIndex:
        filtered = FabricationIndex()
        for (composition, draw), draw_data in index.draw_level.items():
            comp_key = str(composition).strip()
            if comp_key not in relevant_compositions:
                continue
            if self._allow_draw(relevant_map, comp_key, int(draw)):
                filtered.set_draw(comp_key, int(draw), dict(draw_data))
        for (composition, draw, piece), piece_data in index.piece_level.items():
            comp_key = str(composition).strip()
            if comp_key not in relevant_compositions:
                continue
            draw_int = int(draw)
            piece_int = int(piece)
            if not self._allow_draw(relevant_map, comp_key, draw_int):
                continue
            if not self._allow_piece(relevant_map, comp_key, draw_int, piece_int):
                continue
            filtered.set_piece(comp_key, draw_int, piece_int, dict(piece_data))
        return filtered

    @staticmethod
    def _normalise_token(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(text).lower())

    def _filter_candidates_for_relevance(
        self,
        candidates: List[Path],
        relevant_map: Dict[str, Dict[Optional[int], Set[Optional[int]]]],
        relevant_compositions: Set[str],
    ) -> Tuple[List[Path], int, bool]:
        if not relevant_compositions:
            return candidates, 0, False
        composition_tokens = {
            comp: self._normalise_token(comp)
            for comp in relevant_compositions
            if self._normalise_token(comp)
        }
        if not composition_tokens:
            return candidates, 0, False
        draw_tokens: Dict[str, Set[str]] = {}
        for comp, draw_map in relevant_map.items():
            comp_key = self._normalise_token(comp)
            if not comp_key:
                continue
            bucket = draw_tokens.setdefault(comp_key, set())
            for draw, pieces in draw_map.items():
                if draw is not None:
                    bucket.add(self._normalise_token(draw))
                for piece in pieces:
                    if piece is not None:
                        bucket.add(self._normalise_token(f"{draw}{piece}"))
        filtered: List[Path] = []
        skipped = 0
        for path in candidates:
            text = self._normalise_token(path)
            if not text:
                filtered.append(path)
                continue
            matched = False
            for _, token in composition_tokens.items():
                if token and token in text:
                    matched = True
                    break
                draw_set = draw_tokens.get(token)
                if draw_set and any(draw_token in text for draw_token in draw_set if draw_token):
                    matched = True
                    break
            if matched:
                filtered.append(path)
            else:
                skipped += 1
        if not filtered:
            return candidates, 0, True
        return filtered, skipped, False

    def process(
        self,
        paths: List[Path],
        progress: Optional[Callable[[int, int, Optional[str]], None]] = None,
    ) -> SectionProcessResult:
        unique_paths = list(dict.fromkeys(Path(p) for p in paths))

        relevant_map, relevant_compositions = self._load_relevant_map()
        filtered_paths, skipped, reverted = self._filter_candidates_for_relevance(
            unique_paths, relevant_map, relevant_compositions
        )
        if reverted:
            self.log(
                "Fabrication data: relevance filter could not match any files; falling back to full file list."
            )
        elif skipped:
            self.log(
                f"Fabrication data: skipped {skipped} file(s) without matching current annealing composition."
            )

        def _progress(idx: int, total: int) -> None:
            self._check_cancelled()
            if progress is None:
                return
            message: Optional[str] = None
            if 0 < idx <= len(filtered_paths):
                message = f"Parsing {filtered_paths[idx - 1].name}"
            try:
                progress(idx, total, message)
            except Exception:
                pass

        index = build_fabrication_index(
            filtered_paths,
            self.logger,
            progress_callback=_progress,
            cancel_callback=self.is_cancelled,
        )
        self._check_cancelled()
        if relevant_compositions:
            original_draws = len(index.draw_level)
            original_pieces = len(index.piece_level)
            index = self._filter_index(index, relevant_map, relevant_compositions)
            removed_draws = original_draws - len(index.draw_level)
            removed_pieces = original_pieces - len(index.piece_level)
            if removed_draws > 0 or removed_pieces > 0:
                self.log(
                    "Fabrication data: skipped %d draw(s) and %d piece(s) without matching current annealing records."
                    % (removed_draws, removed_pieces)
                )
        table = _fabrication_index_to_frame(index)
        processed: Dict[str, float] = {}
        for path in filtered_paths:
            try:
                processed[str(path)] = float(path.stat().st_mtime)
            except OSError:
                continue
        return SectionProcessResult(
            table=table,
            processed=processed,
            payloads={"fabrication_index": index},
        )

    def refresh(self) -> None:
        super().refresh()
        self._hide_columns(["_source_paths", "_source_path"])

    def _row_sources(self, row: pd.Series) -> List[Path]:
        sources: List[Path] = []
        raw_paths: List[str] = []
        path_values = row.get("_source_paths")
        if isinstance(path_values, (list, tuple, set)):
            raw_paths.extend(str(value) for value in path_values if value)
        fallback = row.get("_source_path")
        if fallback:
            raw_paths.append(str(fallback))
        for entry in dict.fromkeys(raw_paths):
            try:
                sources.append(Path(entry))
            except Exception:
                continue
        return sources


class AnnealingSection(MiniDatabaseSection):
    section_key = "annealing"
    section_title = "Current annealing"
    supported_suffixes = (".txt", ".csv", ".tsv")

    def __init__(
        self,
        logger: logging.Logger,
        log_callback: Callable[[int, str], None],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(logger, log_callback, parent)
        self._pixmap_cache: Dict[Tuple[str, str], Optional[QtGui.QPixmap]] = {}
        if isinstance(self.model, DataFrameModel):
            self.model.set_decoration_provider(self._preview_decoration)
        self._sanitize_graph_columns()
        self._record_groups: Dict[str, List[MeasurementRecord]] = {}
        self.export_button = QtWidgets.QPushButton("Export worksheet…")
        self.export_button.clicked.connect(self._export_worksheet)
        self.controls_layout.addWidget(self.export_button)
        self._update_export_enabled()
        self._refresh_record_groups()
        self._hide_columns(["_group_key", "_sources"])

    def create_right_panel(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        table = QtWidgets.QTableView(parent)
        table.setModel(self.model)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        table.setSortingEnabled(True)
        table.setIconSize(
            QtCore.QSize(ANNEALING_GRAPH_WIDTH, ANNEALING_GRAPH_HEIGHT)
        )
        header = table.verticalHeader()
        if header is not None:
            default_height = ANNEALING_GRAPH_HEIGHT + 24
            header.setDefaultSectionSize(default_height)
            header.setMinimumSectionSize(default_height)
        self.table_view = table
        return table

    def process(
        self,
        paths: List[Path],
        progress: Optional[Callable[[int, int, Optional[str]], None]] = None,
    ) -> SectionProcessResult:
        records: List[MeasurementRecord] = []
        processed: Dict[str, float] = {}
        total = len(paths)
        sanity_failures: List[Tuple[Path, Optional[float]]] = []
        for idx, path in enumerate(paths, start=1):
            self._check_cancelled()
            try:
                df = _load_annealing(path)
            except Exception:
                self.logger.exception("Failed to parse %s", path)
                if progress is not None:
                    try:
                        progress(idx, total, f"Failed: {path.name}")
                    except Exception:
                        pass
                continue
            metadata = _metadata_from_path(path)
            ok, mean_error = _resistance_sanity_check(df)
            if not ok:
                sanity_failures.append((path, mean_error))
            record = MeasurementRecord(
                path=path,
                metadata=metadata,
                dataframe=df,
                sanity_ok=ok,
                sanity_error=mean_error,
            )
            records.append(record)
            try:
                processed[str(path)] = float(path.stat().st_mtime)
            except OSError:
                processed[str(path)] = 0.0
            if progress is not None:
                try:
                    progress(idx, total, f"Parsed {path.name}")
                except Exception:
                    pass
        table = _annealing_records_to_frame(records, self.logger)
        if sanity_failures:
            preview = ", ".join(p.name for p, _ in sanity_failures[:5])
            if len(sanity_failures) > 5:
                preview += ", …"
            errors = [err for _, err in sanity_failures if isinstance(err, (int, float))]
            worst = f" (worst error {max(errors) * 100:.1f}%)" if errors else ""
            summary = (
                f"{self.section_title}: R≈V/I sanity check failed for {len(sanity_failures)} file(s){worst}"
            )
            if preview:
                summary += f": {preview}"
            self.log(summary, level=logging.WARNING)
            self.logger.warning(summary)
        return SectionProcessResult(
            table=table,
            processed=processed,
            payloads={"annealing_records": records},
        )

    def refresh(self) -> None:
        super().refresh()
        self._sanitize_graph_columns()
        self._hide_columns(["_group_key", "_sources"])
        self._refresh_record_groups()
        self._update_export_enabled()

    def _update_export_enabled(self) -> None:
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(not self.data.table.empty)

    def _refresh_record_groups(self) -> None:
        grouped: Dict[str, List[MeasurementRecord]] = {}
        try:
            payload = self.store.load_payload("annealing_records")
        except Exception:
            payload = None
        if isinstance(payload, list):
            for record in payload:
                metadata = getattr(record, "metadata", None)
                if metadata is None:
                    continue
                composition = getattr(metadata, "composition_token", None)
                draw = getattr(metadata, "draw_x", None)
                piece = getattr(metadata, "piece_y", None)
                if composition is None or draw is None or piece is None:
                    continue
                try:
                    key = f"{composition}|{int(draw)}|{int(piece)}"
                except (TypeError, ValueError):
                    continue
                grouped.setdefault(key, []).append(record)
        self._record_groups = grouped
        self._invalidate_previews()

    def _sanitize_graph_columns(self) -> None:
        frame = self.data.table if isinstance(self.data.table, pd.DataFrame) else pd.DataFrame()
        if frame.empty:
            return
        changed = False
        for column in ("Graph — 1000 mA", "Graph — low mA"):
            if column not in frame.columns:
                continue
            series = frame[column]
            if not hasattr(series, "apply"):
                continue
            cleaned = series.apply(
                lambda value: None
                if isinstance(value, (QtGui.QPixmap, QtGui.QImage))
                else value
            )
            if not cleaned.equals(series):
                frame[column] = cleaned
                changed = True
        desired_order = [
            "Composition",
            "Microwire",
            "Graph — 1000 mA",
            "Graph — low mA",
            "Low current setpoint",
            "Updated",
            "_group_key",
            "_sources",
        ]
        current_columns = list(frame.columns)
        preferred = [col for col in desired_order if col in frame.columns]
        remainder = [col for col in current_columns if col not in desired_order]
        new_order = preferred + remainder
        if new_order != current_columns and new_order:
            frame = frame.loc[:, new_order]
            changed = True
        if changed:
            self.data.table = frame
            if isinstance(self.model, DataFrameModel):
                self.model.set_frame(frame)
                self._auto_fit_columns()
            try:
                self.store.save(self.data)
            except Exception:
                pass
        self._invalidate_previews()

    def _invalidate_previews(self) -> None:
        self._pixmap_cache.clear()
        if isinstance(self.model, DataFrameModel):
            try:
                self.model.layoutChanged.emit()
            except Exception:
                pass

    def _preview_decoration(
        self,
        row: pd.Series,
        column: str,
    ) -> Optional[QtGui.QPixmap]:
        if column not in {"Graph — 1000 mA", "Graph — low mA"}:
            return None
        key = row.get("_group_key")
        if not isinstance(key, str) or not key:
            return None
        cache_key = (key, column)
        if cache_key in self._pixmap_cache:
            return self._pixmap_cache[cache_key]
        records = self._record_groups.get(key)
        if not records:
            self._pixmap_cache[cache_key] = None
            return None
        high_record, low_record = _select_high_low_pair(records)
        target = high_record if column == "Graph — 1000 mA" else low_record
        pixmap = _render_measurement_pixmap(target, self.logger)
        self._pixmap_cache[cache_key] = pixmap
        return pixmap

    def _row_sources(self, row: pd.Series) -> List[Path]:
        sources: List[Path] = []
        raw_sources = row.get("_sources")
        if isinstance(raw_sources, (list, tuple)):
            for entry in raw_sources:
                if not entry:
                    continue
                try:
                    sources.append(Path(entry))
                except Exception:
                    continue
        key = row.get("_group_key")
        if isinstance(key, str):
            for record in self._record_groups.get(key, []):
                path = getattr(record, "path", None)
                if path:
                    try:
                        candidate = Path(path)
                    except Exception:
                        continue
                    if candidate not in sources:
                        sources.append(candidate)
        return sources

    def _export_worksheet(self) -> None:
        records = self.store.load_payload("annealing_records")
        if not isinstance(records, list) or not records:
            QtWidgets.QMessageBox.information(
                self,
                self.section_title,
                "Process current annealing files before exporting.",
            )
            return

        path_str, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save annealing worksheet",
            str(Path.cwd() / "annealing_summary.xlsx"),
            "Excel files (*.xlsx)",
        )
        if not path_str:
            return
        output_path = Path(path_str)
        if output_path.suffix.lower() != ".xlsx":
            output_path = output_path.with_suffix(".xlsx")

        try:
            import xlsxwriter  # type: ignore[import-not-found]
        except ImportError:
            QtWidgets.QMessageBox.warning(
                self,
                self.section_title,
                "xlsxwriter is required to export worksheets.",
            )
            return

        grouped: Dict[Tuple[str, int, int], List[MeasurementRecord]] = {}
        for record in records:
            metadata = getattr(record, "metadata", None)
            if metadata is None:
                continue
            draw_x = getattr(metadata, "draw_x", None)
            piece_y = getattr(metadata, "piece_y", None)
            composition = getattr(metadata, "composition_token", None)
            if composition is None or draw_x is None or piece_y is None:
                continue
            grouped.setdefault((str(composition), int(draw_x), int(piece_y)), []).append(record)

        if not grouped:
            QtWidgets.QMessageBox.warning(
                self,
                self.section_title,
                "No complete microwire records available for export.",
            )
            return

        try:
            with TemporaryDirectory() as tmpdir:
                workbook = xlsxwriter.Workbook(str(output_path))
                try:
                    worksheet = workbook.add_worksheet("Summary")
                    header_format = workbook.add_format({"bold": True})
                    headers = [
                        "Composition",
                        "Microwire",
                        "1000 mA file",
                        "Low mA (mA)",
                        "Low mA file",
                        "Graph — 1000 mA",
                        "Graph — low mA",
                    ]
                    worksheet.write_row(0, 0, headers, header_format)
                    worksheet.set_column(0, 1, 18)
                    worksheet.set_column(2, 4, 22)
                    worksheet.set_column(5, 6, 42)

                    plot_dir = Path(tmpdir)
                    row_idx = 1
                    for (composition, draw_x, piece_y), recs in sorted(grouped.items()):
                        high_record, low_record = _select_high_low_pair(recs)

                        microwire_label = _microwire_label(draw_x, piece_y)
                        low_setpoint = (
                            low_record.metadata.setpoint_mA if low_record else None
                        )
                        high_file = (
                            high_record.metadata.file_name if high_record else ""
                        )
                        low_file = (
                            low_record.metadata.file_name if low_record else ""
                        )
                        formatted_low = _format_setpoint(low_setpoint)

                        worksheet.write_row(
                            row_idx,
                            0,
                            [
                                composition,
                                microwire_label,
                                high_file,
                                formatted_low,
                                low_file,
                                "",
                                "",
                            ],
                        )

                        worksheet.set_row(row_idx, 160)

                        def _plot(record: MeasurementRecord) -> Optional[Path]:
                            if record is None:
                                return None
                            try:
                                plot_path = _plot_measurement_matplotlib(
                                    record.dataframe,
                                    Path(record.path),
                                    plot_dir,
                                    DEFAULT_FIGSIZE,
                                )
                            except Exception:
                                self.logger.exception(
                                    "Failed to render plot for %s", record.path
                                )
                                return None
                            return plot_path

                        high_plot = _plot(high_record) if high_record else None
                        low_plot = _plot(low_record) if low_record else None
                        image_options = {"x_scale": 0.6, "y_scale": 0.6}
                        if high_plot is not None:
                            worksheet.insert_image(
                                row_idx,
                                5,
                                str(high_plot),
                                image_options,
                            )
                        if low_plot is not None:
                            worksheet.insert_image(
                                row_idx,
                                6,
                                str(low_plot),
                                image_options,
                            )
                        row_idx += 1
                finally:
                    workbook.close()
        except Exception as exc:
            try:
                if output_path.exists():
                    output_path.unlink()
            except OSError:
                pass
            QtWidgets.QMessageBox.critical(
                self,
                self.section_title,
                f"Failed to export worksheet:\n{exc}",
            )
            return

        self.log(f"Current annealing worksheet saved to {output_path}")
        QtWidgets.QMessageBox.information(
            self,
            self.section_title,
            f"Worksheet saved to {output_path}",
        )


class MicroscopeSection(MiniDatabaseSection):
    section_key = "microscope"
    section_title = "Microscope OCR"
    supported_suffixes = MICROSCOPE_EXTENSIONS

    def __init__(
        self,
        logger: logging.Logger,
        log_callback: Callable[[int, str], None],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        self._overrides: Dict[str, Dict[str, float]] = {}
        self._selected_key: str | None = None
        self._ocr_debug_enabled = False
        super().__init__(logger, log_callback, parent)
        stored_overrides = self.data.extra.get("overrides")
        if isinstance(stored_overrides, dict):
            self._overrides = {
                str(key): {k: float(v) for k, v in value.items() if isinstance(v, (int, float))}
                for key, value in stored_overrides.items()
                if isinstance(value, dict)
            }
        if not self.data.table.empty:
            self._apply_overrides_to_table()
        self._update_hidden_columns()

    def create_right_panel(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, parent)
        table = QtWidgets.QTableView(splitter)
        table.setModel(self.model)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        table.setSortingEnabled(True)
        self.table_view = table
        selection_model = table.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(self._handle_selection_changed)

        preview_container = QtWidgets.QWidget(splitter)
        preview_layout = QtWidgets.QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)

        self.preview_label = QtWidgets.QLabel("Select a row to preview the image.")
        self.preview_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(220, 220)
        self.preview_label.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        preview_layout.addWidget(self.preview_label, 1)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)

        self.d_edit = QtWidgets.QLineEdit()
        self.d_edit.setPlaceholderText("auto")
        self.D_edit = QtWidgets.QLineEdit()
        self.D_edit.setPlaceholderText("auto")
        form.addRow("d (µm)", self.d_edit)
        form.addRow("D (µm)", self.D_edit)
        preview_layout.addLayout(form)

        button_row = QtWidgets.QHBoxLayout()
        self.apply_override_button = QtWidgets.QPushButton("Apply override")
        self.apply_override_button.clicked.connect(self._apply_override)
        button_row.addWidget(self.apply_override_button)
        self.clear_override_button = QtWidgets.QPushButton("Clear override")
        self.clear_override_button.clicked.connect(self._clear_override)
        button_row.addWidget(self.clear_override_button)
        preview_layout.addLayout(button_row)

        return splitter

    def set_ocr_debug_enabled(self, enabled: bool) -> None:
        self._ocr_debug_enabled = bool(enabled)

    def _ocr_debug_callback(self, path: Path, result: MicroscopeOCRResult) -> None:
        if not self._ocr_debug_enabled:
            return
        values = [
            f"{float(value):.3f}"
            for value in result.values
            if isinstance(value, (int, float)) and math.isfinite(float(value))
        ]
        value_text = ", ".join(values) if values else "—"
        sample_texts: List[str] = []
        for detection in result.detections:
            raw = getattr(detection, "text", None)
            if raw:
                cleaned = str(raw).replace("\n", " ").strip()
                if cleaned:
                    sample_texts.append(cleaned)
        for text in result.texts:
            cleaned = str(text).replace("\n", " ").strip()
            if cleaned:
                sample_texts.append(cleaned)
        text_preview = " | ".join(sample_texts) if sample_texts else "—"
        message = f"OCR debug {Path(path).name}: values={value_text}"
        message += f"; text={text_preview}"
        self.log(message, level=logging.INFO)

    def _update_hidden_columns(self) -> None:
        if not isinstance(self.table_view, QtWidgets.QTableView):
            return
        model = self.table_view.model()
        if model is None:
            return
        for column_name in ("_key", "_images"):
            try:
                column_index = list(model.frame().columns).index(column_name)  # type: ignore[arg-type]
            except Exception:
                continue
            self.table_view.setColumnHidden(column_index, True)

    def _row_sources(self, row: pd.Series) -> List[Path]:
        sources: List[Path] = []
        images = row.get("_images")
        if isinstance(images, (list, tuple, set)):
            for entry in images:
                if not entry:
                    continue
                try:
                    sources.append(Path(entry))
                except Exception:
                    continue
        return sources

    def _selected_row(self) -> Optional[pd.Series]:
        if not isinstance(self.table_view, QtWidgets.QTableView):
            return None
        selection = self.table_view.selectionModel()
        if selection is None:
            return None
        indexes = selection.selectedRows()
        if not indexes:
            return None
        row = indexes[0].row()
        try:
            return self.data.table.iloc[row]
        except Exception:
            return None

    def _handle_selection_changed(self, *_: Any) -> None:
        row = self._selected_row()
        if row is None:
            self._selected_key = None
            self.preview_label.setText("Select a row to preview the image.")
            self.d_edit.clear()
            self.D_edit.clear()
            return
        key = row.get("_key")
        self._selected_key = str(key) if key is not None else None
        d_value = row.get("d (µm)")
        D_value = row.get("D (µm)")
        self.d_edit.setText("" if d_value is None or (isinstance(d_value, float) and math.isnan(d_value)) else f"{float(d_value):.3f}")
        self.D_edit.setText("" if D_value is None or (isinstance(D_value, float) and math.isnan(D_value)) else f"{float(D_value):.3f}")
        images = row.get("_images") if isinstance(row.get("_images"), list) else []
        if images:
            first = Path(images[0])
            if first.exists():
                pixmap = QtGui.QPixmap(str(first))
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        self.preview_label.size(),
                        QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                        QtCore.Qt.TransformationMode.SmoothTransformation,
                    )
                    self.preview_label.setPixmap(scaled)
                    return
        self.preview_label.setPixmap(QtGui.QPixmap())
        self.preview_label.setText("No preview available.")

    def _apply_override(self) -> None:
        if not self._selected_key:
            return
        d_text = self.d_edit.text().strip()
        D_text = self.D_edit.text().strip()
        override: Dict[str, float] = {}
        if d_text:
            try:
                override["d"] = float(d_text)
            except ValueError:
                QtWidgets.QMessageBox.warning(self, self.section_title, "Invalid d value.")
                return
        if D_text:
            try:
                override["D"] = float(D_text)
            except ValueError:
                QtWidgets.QMessageBox.warning(self, self.section_title, "Invalid D value.")
                return
        if override:
            self._overrides[self._selected_key] = override
        else:
            self._overrides.pop(self._selected_key, None)
        self._store_overrides()

    def _clear_override(self) -> None:
        if not self._selected_key:
            return
        if self._selected_key in self._overrides:
            self._overrides.pop(self._selected_key, None)
            self._store_overrides()
        self.d_edit.clear()
        self.D_edit.clear()

    def _store_overrides(self) -> None:
        self.data.extra["overrides"] = self._overrides
        self.store.save(self.data)
        self._apply_overrides_to_table()
        self._update_hidden_columns()

    def _apply_overrides_to_table(self) -> None:
        frame = self.data.table.copy()
        if frame.empty:
            self.model.set_frame(frame)
            return
        for index, row in frame.iterrows():
            key = str(row.get("_key"))
            override = self._overrides.get(key)
            d_value = row.get("d (µm)")
            D_value = row.get("D (µm)")
            if override:
                if "d" in override:
                    d_value = override.get("d")
                if "D" in override:
                    D_value = override.get("D")
            ratio = None
            if isinstance(d_value, (int, float)) and isinstance(D_value, (int, float)) and D_value:
                try:
                    ratio = float(d_value) / float(D_value)
                except ZeroDivisionError:
                    ratio = None
            frame.at[index, "d (µm)"] = d_value
            frame.at[index, "D (µm)"] = D_value
            frame.at[index, "d/D"] = round(ratio, 3) if ratio is not None else None
        self.data.table = frame
        self.model.set_frame(frame)
        self._auto_fit_columns()

    def refresh(self) -> None:
        super().refresh()
        self._apply_overrides_to_table()
        self._update_hidden_columns()

    def process(
        self,
        paths: List[Path],
        progress: Optional[Callable[[int, int, Optional[str]], None]] = None,
    ) -> SectionProcessResult:
        def _progress(idx: int, total: int) -> None:
            self._check_cancelled()
            if progress is None:
                return
            message = None
            if 0 < idx <= len(paths):
                try:
                    message = f"Grouping {Path(paths[idx - 1]).name}"
                except Exception:
                    message = None
            try:
                progress(idx, total, message)
            except Exception:
                pass

        debug_cb = self._ocr_debug_callback if self._ocr_debug_enabled else None
        index = _group_microscope_measurements(
            paths,
            self.logger,
            progress_callback=_progress if progress is not None else None,
            debug_callback=debug_cb,
        )
        expected_keys: Set[Tuple[str, int, int]] = set()
        try:
            annealing_records = MiniDatabaseStore("annealing").load_payload(
                "annealing_records"
            )
        except Exception:
            annealing_records = None
        if isinstance(annealing_records, list):
            for record in annealing_records:
                metadata = getattr(record, "metadata", None)
                if metadata is None:
                    continue
                composition = getattr(metadata, "composition_token", None)
                draw = getattr(metadata, "draw_x", None)
                piece = getattr(metadata, "piece_y", None)
                if not composition or draw is None or piece is None:
                    continue
                try:
                    key = (str(composition), int(draw), int(piece))
                except (TypeError, ValueError):
                    continue
                expected_keys.add(key)
        for key in expected_keys:
            index.setdefault(key, MicroscopeMeasurements())
        self._check_cancelled()
        # Retain overrides only for existing keys
        filtered_overrides = {
            key: value
            for key, value in self._overrides.items()
            if any(
                key == f"{comp}|{draw}|{piece}"
                for comp, draw, piece in index.keys()
            )
        }
        self._overrides = filtered_overrides
        table = _microscope_index_to_frame(index, filtered_overrides)
        processed: Dict[str, float] = {}
        for path in paths:
            try:
                processed[str(path)] = float(path.stat().st_mtime)
            except OSError:
                continue
        total_records = len(index)
        def _count_measurements(entries: Sequence[MicroscopeDetection]) -> int:
            count = 0
            for entry in entries:
                value = getattr(entry, "value", None)
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    numeric = math.nan
                if math.isfinite(numeric) and numeric > 0:
                    count += 1
            return count

        total_core = sum(_count_measurements(m.core) for m in index.values())
        total_glass = sum(_count_measurements(m.glass) for m in index.values())
        if total_core or total_glass:
            self.log(
                f"Microscope OCR detected {total_core} core and {total_glass} glass diameter(s) across {total_records} microwire(s).",
                level=logging.INFO,
            )
        else:
            self.log(
                "Microscope OCR completed but no diameters were detected. Ensure the PaddleOCR models are installed and the microscope captures contain visible annotations.",
                level=logging.WARNING,
            )
        return SectionProcessResult(
            table=table,
            processed=processed,
            payloads={"microscope_index": index},
            extra={"overrides": filtered_overrides},
        )

    @property
    def overrides(self) -> Dict[str, Dict[str, float]]:
        return dict(self._overrides)


class VideoSection(MiniDatabaseSection):
    section_key = "videos"
    section_title = "Fabrication videos"
    supported_suffixes = VIDEO_EXTENSIONS

    def __init__(
        self,
        logger: logging.Logger,
        log_callback: Callable[[int, str], None],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(logger, log_callback, parent)
        self.source_button.hide()
        self.refresh_button.setText("Start video OCR")
        self._hide_columns(["_sources"])

    def process(
        self,
        paths: List[Path],
        progress: Optional[Callable[[int, int, Optional[str]], None]] = None,
    ) -> SectionProcessResult:
        unique_paths = list(dict.fromkeys(Path(p) for p in paths))

        def _progress(idx: int, total: int) -> None:
            self._check_cancelled()
            if progress is None:
                return
            message: Optional[str] = None
            if 0 < idx <= len(unique_paths):
                message = f"Analysing {unique_paths[idx - 1].name}"
            try:
                progress(idx, total, message)
            except Exception:
                pass

        index = _collect_video_metrics(
            unique_paths,
            self.logger,
            progress_callback=_progress if progress is not None else None,
        )
        self._check_cancelled()
        table = _video_index_to_frame(index)
        processed: Dict[str, float] = {}
        for path in unique_paths:
            try:
                processed[str(path)] = float(path.stat().st_mtime)
            except OSError:
                continue
        return SectionProcessResult(
            table=table,
            processed=processed,
            payloads={"video_index": index},
        )

    def refresh(self) -> None:
        super().refresh()
        self._hide_columns(["_sources"])

    def _row_sources(self, row: pd.Series) -> List[Path]:
        sources: List[Path] = []
        raw = row.get("_sources")
        if isinstance(raw, (list, tuple, set)):
            for entry in raw:
                if not entry:
                    continue
                try:
                    sources.append(Path(entry))
                except Exception:
                    continue
        return sources



class StrainSection(MiniDatabaseSection):
    section_key = "strain"
    section_title = "Strain data"
    supported_suffixes: tuple[str, ...] = ()
    recursive_search = False

    COLUMN_COMPOSITION = "Composition"
    COLUMN_MICROWIRE = "Microwire"
    COLUMN_DRAW = "Draw"
    COLUMN_PIECE = "Piece"
    COLUMN_D = "d (µm)"
    COLUMN_MASS = "m"
    COLUMN_M_LENGTH = "M length"
    COLUMN_A_LENGTH = "A length"
    COLUMN_STRAIN = "Strain"
    COLUMN_BROKE = "Broke"
    TABLE_COLUMNS = [
        COLUMN_COMPOSITION,
        COLUMN_MICROWIRE,
        COLUMN_DRAW,
        COLUMN_PIECE,
        COLUMN_D,
        COLUMN_MASS,
        COLUMN_M_LENGTH,
        COLUMN_A_LENGTH,
        COLUMN_STRAIN,
        COLUMN_BROKE,
    ]
    HIDDEN_COLUMNS = (COLUMN_DRAW, COLUMN_PIECE, COLUMN_BROKE)

    def __init__(
        self,
        logger: logging.Logger,
        log_callback: Callable[[int, str], None],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        self._wire_choices: Dict[str, Dict[str, tuple[int, int]]] = {}
        self._d_lookup: Dict[tuple[str, int, int], float] = {}
        self._suspend_auto_fill = False
        self._editing_index: Optional[int] = None
        self._editing_key: Optional[tuple[str, int, int]] = None
        self._selected_wire_key: Optional[tuple[str, int, int]] = None
        self._strain_offset: float = 7.0
        super().__init__(logger, log_callback, parent)
        self.source_button.hide()
        self.refresh_button.hide()
        if hasattr(self, "open_sources_button"):
            self.open_sources_button.hide()
        self.sources_list.hide()
        self.sources_list.setMaximumWidth(0)
        self.status_label.setWordWrap(True)
        stored_offset = None
        if isinstance(self.data.extra, dict):
            stored_offset = self.data.extra.get("strain_offset")
        if isinstance(stored_offset, (int, float)):
            self._strain_offset = float(stored_offset)
        else:
            if not isinstance(self.data.extra, dict):
                self.data.extra = {}
            self.data.extra["strain_offset"] = self._strain_offset
            self.store.save(self.data)
        if hasattr(self, "strain_offset_spin"):
            blocked = self.strain_offset_spin.blockSignals(True)
            self.strain_offset_spin.setValue(self._strain_offset)
            self.strain_offset_spin.blockSignals(blocked)
        self._ensure_table_structure()
        self._refresh_table_view()
        self._load_reference_data()
        self._sync_payload()
        if hasattr(self, "composition_combo"):
            self._update_composition_suggestions()
        self._update_status()

    def create_right_panel(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget(parent)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        form_container = QtWidgets.QWidget(container)
        form_layout = QtWidgets.QHBoxLayout(form_container)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)

        def _add_field(label_text: str, widget: QtWidgets.QWidget) -> None:
            field_box = QtWidgets.QWidget(form_container)
            column = QtWidgets.QVBoxLayout(field_box)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            label = QtWidgets.QLabel(label_text, field_box)
            label.setAlignment(
                QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
            )
            column.addWidget(label)
            column.addWidget(widget)
            form_layout.addWidget(field_box)

        self.composition_combo = QtWidgets.QComboBox(form_container)
        self.composition_combo.setEditable(True)
        self.composition_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.composition_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        line_edit = self.composition_combo.lineEdit()
        if line_edit is not None:
            line_edit.textEdited.connect(self._composition_text_edited)
        self.composition_combo.currentTextChanged.connect(self._composition_changed)
        _add_field("Composition", self.composition_combo)

        self.microwire_combo = QtWidgets.QComboBox(form_container)
        self.microwire_combo.currentIndexChanged.connect(self._microwire_changed)
        _add_field("Microwire", self.microwire_combo)

        self.d_edit = QtWidgets.QLineEdit(form_container)
        self.d_edit.setPlaceholderText("auto")
        self.d_edit.textChanged.connect(self._update_mass_display)
        _add_field(self.COLUMN_D, self.d_edit)

        self.mass_display = QtWidgets.QLineEdit(form_container)
        self.mass_display.setReadOnly(True)
        _add_field(self.COLUMN_MASS, self.mass_display)

        self.M_length_edit = QtWidgets.QLineEdit(form_container)
        self.M_length_edit.setPlaceholderText("mm")
        self.M_length_edit.textChanged.connect(self._update_strain_display)
        _add_field(self.COLUMN_M_LENGTH, self.M_length_edit)

        self.A_length_edit = QtWidgets.QLineEdit(form_container)
        self.A_length_edit.setPlaceholderText("mm or '-' if broke")
        self.A_length_edit.textChanged.connect(self._update_strain_display)
        _add_field(self.COLUMN_A_LENGTH, self.A_length_edit)

        self.strain_offset_spin = QtWidgets.QDoubleSpinBox(form_container)
        self.strain_offset_spin.setDecimals(6)
        self.strain_offset_spin.setRange(-1000.0, 1000.0)
        self.strain_offset_spin.setSingleStep(0.1)
        self.strain_offset_spin.setValue(self._strain_offset)
        self.strain_offset_spin.valueChanged.connect(self._strain_offset_changed)
        _add_field("C offset", self.strain_offset_spin)

        self.strain_display = QtWidgets.QLineEdit(form_container)
        self.strain_display.setReadOnly(True)
        _add_field(f"{self.COLUMN_STRAIN} (%)", self.strain_display)

        form_layout.addStretch(1)

        layout.addWidget(form_container)

        button_row = QtWidgets.QHBoxLayout()
        self.add_update_button = QtWidgets.QPushButton("Add entry")
        self.add_update_button.clicked.connect(self._save_entry)
        button_row.addWidget(self.add_update_button)

        self.clear_button = QtWidgets.QPushButton("Clear")
        self.clear_button.clicked.connect(self._clear_form)
        button_row.addWidget(self.clear_button)

        self.delete_button = QtWidgets.QPushButton("Remove entry")
        self.delete_button.clicked.connect(self._delete_selected)
        self.delete_button.setEnabled(False)
        button_row.addWidget(self.delete_button)

        button_row.addStretch(1)

        self.refresh_sources_button = QtWidgets.QPushButton("Reload data")
        self.refresh_sources_button.clicked.connect(self._reload_references_clicked)
        button_row.addWidget(self.refresh_sources_button)

        self.export_button = QtWidgets.QPushButton("Export to Excel…")
        self.export_button.clicked.connect(self._export_to_excel)
        self.export_button.setEnabled(False)
        button_row.addWidget(self.export_button)

        layout.addLayout(button_row)

        self.table_view = QtWidgets.QTableView(container)
        self.table_view.setModel(self.model)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table_view.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table_view.setSortingEnabled(True)
        selection_model = self.table_view.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(self._handle_table_selection)
        layout.addWidget(self.table_view, 1)

        return container

    def _update_status(self) -> None:
        entries = len(self.data.table.index) if isinstance(self.data.table, pd.DataFrame) else 0
        entry_word = "entry" if entries == 1 else "entries"
        available = self._available_wire_count()
        if not self._wire_choices:
            suffix = "Process current annealing data to populate suggestions."
        elif available:
            suffix = f"{available} microwire(s) awaiting strain logging."
        else:
            suffix = "All processed microwires are represented."
        self.status_label.setText(f"{entries} strain {entry_word} stored. {suffix}")
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(entries > 0)
        if hasattr(self, "delete_button"):
            has_selection = self._editing_index is not None and entries > 0
            self.delete_button.setEnabled(has_selection)

    def refresh(self) -> None:
        self._load_reference_data()
        if hasattr(self, "composition_combo"):
            self._update_composition_suggestions()
        self._update_status()

    def _composition_text_edited(self, _: str) -> None:
        if self._suspend_auto_fill:
            return
        self.composition_combo.showPopup()
        self._update_microwire_options()

    def _composition_changed(self, _: str) -> None:
        if self._suspend_auto_fill:
            return
        self._update_microwire_options()

    def _microwire_changed(self) -> None:
        comp = self.composition_combo.currentText().strip()
        data = self.microwire_combo.currentData()
        key: Optional[tuple[int, int]] = None
        if isinstance(data, tuple):
            key = (int(data[0]), int(data[1]))
        else:
            parsed = _microwire_tuple_from_label(self.microwire_combo.currentText())
            if parsed:
                key = (int(parsed[0]), int(parsed[1]))
        if key is None:
            self._selected_wire_key = None
            if not self._suspend_auto_fill:
                self.d_edit.clear()
                self._update_mass_display()
            return
        self._selected_wire_key = (comp, key[0], key[1])
        if self._suspend_auto_fill:
            return
        d_value = self._d_lookup.get(self._selected_wire_key)
        if d_value is not None:
            self.d_edit.setText(f"{d_value:.4f}")
        self._update_mass_display()

    def _update_mass_display(self) -> None:
        value = _parse_strain_float(self.d_edit.text())
        mass = self._calculate_mass(value)
        if mass is None:
            self.mass_display.setText("")
        else:
            self.mass_display.setText(f"{mass:.6f}")

    def _update_strain_display(self) -> None:
        text = self.A_length_edit.text().strip()
        if text == "-" or text.lower() == "broke":
            self.strain_display.setText("broke")
            return
        m_length = _parse_strain_float(self.M_length_edit.text())
        a_length = _parse_strain_float(text)
        if m_length in (None, 0) or a_length is None:
            self.strain_display.setText("")
            return
        percent = self._compute_strain_percent(m_length, a_length)
        if percent is None:
            self.strain_display.setText("")
            return
        self.strain_display.setText(f"{percent:.3f}")

    def _handle_table_selection(self, *_: Any) -> None:
        self._load_row(self._selected_row_index())

    def _selected_row_index(self) -> Optional[int]:
        if not isinstance(self.table_view, QtWidgets.QTableView):
            return None
        selection = self.table_view.selectionModel()
        if selection is None:
            return None
        rows = selection.selectedRows()
        if not rows:
            return None
        return rows[0].row()

    def _load_row(self, row_index: Optional[int]) -> None:
        if row_index is None or row_index < 0 or row_index >= len(self.data.table.index):
            self._editing_index = None
            self._editing_key = None
            self._suspend_auto_fill = True
            self.composition_combo.setEditText("")
            self.microwire_combo.clear()
            self.d_edit.clear()
            self.mass_display.clear()
            self.M_length_edit.clear()
            self.A_length_edit.clear()
            self.strain_display.clear()
            self._suspend_auto_fill = False
            self.add_update_button.setText("Add entry")
            self._update_status()
            return

        row = self.data.table.iloc[row_index]
        composition = str(row.get(self.COLUMN_COMPOSITION) or "").strip()
        microwire = str(row.get(self.COLUMN_MICROWIRE) or "").strip()
        draw = row.get(self.COLUMN_DRAW)
        piece = row.get(self.COLUMN_PIECE)
        key: Optional[tuple[str, int, int]] = None
        if pd.notna(draw) and pd.notna(piece):
            try:
                key = (composition, int(float(draw)), int(float(piece)))
            except (TypeError, ValueError):
                key = None
        if key is None and microwire:
            parsed = _microwire_tuple_from_label(microwire)
            if parsed:
                key = (composition, int(parsed[0]), int(parsed[1]))
        self._editing_index = row_index
        self._editing_key = key

        self._suspend_auto_fill = True
        self.composition_combo.setEditText(composition)
        self._update_composition_suggestions()
        self._update_microwire_options()
        if microwire:
            idx = self.microwire_combo.findText(microwire)
            if idx >= 0:
                self.microwire_combo.setCurrentIndex(idx)
            elif key is not None:
                self.microwire_combo.insertItem(0, microwire, (key[1], key[2]))
                self.microwire_combo.setCurrentIndex(0)
        d_value = _parse_strain_float(row.get(self.COLUMN_D))
        self.d_edit.setText("" if d_value is None else f"{d_value:.4f}")
        mass_value = _parse_strain_float(row.get(self.COLUMN_MASS))
        self.mass_display.setText("" if mass_value is None else f"{mass_value:.6f}")
        m_length = _parse_strain_float(row.get(self.COLUMN_M_LENGTH))
        self.M_length_edit.setText("" if m_length is None else f"{m_length:.4f}")
        a_entry = row.get(self.COLUMN_A_LENGTH)
        if isinstance(a_entry, str) and a_entry.strip():
            self.A_length_edit.setText(a_entry)
        else:
            a_length = _parse_strain_float(a_entry)
            self.A_length_edit.setText("" if a_length is None else f"{a_length:.4f}")
        strain_value = row.get(self.COLUMN_STRAIN)
        if isinstance(strain_value, str) and strain_value.strip().lower() == "broke":
            self.strain_display.setText("broke")
        else:
            strain_float = _parse_strain_float(strain_value)
            self.strain_display.setText("" if strain_float is None else f"{strain_float:.3f}")
        self._suspend_auto_fill = False
        self.add_update_button.setText("Update entry")
        self._update_status()

    def _clear_form(self) -> None:
        self._editing_index = None
        self._editing_key = None
        self._selected_wire_key = None
        if isinstance(self.table_view, QtWidgets.QTableView):
            self.table_view.clearSelection()
        self._suspend_auto_fill = True
        self.composition_combo.setEditText("")
        self.microwire_combo.clear()
        self.d_edit.clear()
        self.mass_display.clear()
        self.M_length_edit.clear()
        self.A_length_edit.clear()
        self.strain_display.clear()
        self._suspend_auto_fill = False
        self.add_update_button.setText("Add entry")
        if hasattr(self, "delete_button"):
            self.delete_button.setEnabled(False)
        self._update_composition_suggestions()
        self._update_status()

    def _save_entry(self) -> None:
        composition = self.composition_combo.currentText().strip()
        if not composition:
            QtWidgets.QMessageBox.warning(self, self.section_title, "Enter a composition.")
            return
        if self.microwire_combo.count() == 0:
            QtWidgets.QMessageBox.warning(self, self.section_title, "Select a microwire.")
            return
        label = self.microwire_combo.currentText().strip()
        data = self.microwire_combo.currentData()
        key: Optional[tuple[str, int, int]] = None
        if isinstance(data, tuple):
            key = (composition, int(data[0]), int(data[1]))
        elif self._selected_wire_key and self._selected_wire_key[0] == composition:
            key = self._selected_wire_key
        else:
            parsed = _microwire_tuple_from_label(label)
            if parsed:
                key = (composition, int(parsed[0]), int(parsed[1]))
        if key is None:
            QtWidgets.QMessageBox.warning(
                self,
                self.section_title,
                "Unable to determine the draw/piece for the selected microwire.",
            )
            return
        used = self._used_wire_keys()
        if self._editing_key in used:
            used.discard(self._editing_key)
        if key in used:
            QtWidgets.QMessageBox.warning(
                self,
                self.section_title,
                "This microwire already has a strain entry.",
            )
            return

        d_value = _parse_strain_float(self.d_edit.text())
        if d_value is None or d_value <= 0:
            QtWidgets.QMessageBox.warning(self, self.section_title, "Enter a valid diameter.")
            return
        mass_value = self._calculate_mass(d_value)

        m_length = _parse_strain_float(self.M_length_edit.text())
        a_text = self.A_length_edit.text().strip()
        broke = a_text == "-" or a_text.lower() == "broke"
        a_length = None if broke else _parse_strain_float(a_text)
        if not broke and a_text and a_length is None:
            QtWidgets.QMessageBox.warning(self, self.section_title, "Enter a valid A length or '-' if the wire broke.")
            return
        strain_percent = None
        if broke:
            strain_display = "broke"
            a_display: object = "-"
        else:
            a_display = a_length
            if m_length not in (None, 0) and a_length is not None:
                strain_percent = self._compute_strain_percent(m_length, a_length)
            strain_display = "" if strain_percent is None else f"{strain_percent:.3f}"

        row_data = {
            self.COLUMN_COMPOSITION: composition,
            self.COLUMN_MICROWIRE: label,
            self.COLUMN_DRAW: key[1],
            self.COLUMN_PIECE: key[2],
            self.COLUMN_D: d_value,
            self.COLUMN_MASS: mass_value,
            self.COLUMN_M_LENGTH: m_length,
            self.COLUMN_A_LENGTH: a_display,
            self.COLUMN_STRAIN: strain_display if broke else (None if strain_percent is None else round(strain_percent, 3)),
            self.COLUMN_BROKE: broke,
        }

        if self._editing_index is not None and 0 <= self._editing_index < len(self.data.table.index):
            idx = self._editing_index
        else:
            idx = len(self.data.table.index)
        self.data.table.loc[idx, :] = row_data
        self._editing_index = idx
        self._editing_key = key
        self._save_table()
        self._select_key(key)
        self.log(
            f"Strain: recorded {composition} {label}"
        )

    def _delete_selected(self) -> None:
        row_index = self._selected_row_index()
        if row_index is None:
            return
        self.data.table = self.data.table.drop(index=row_index).reset_index(drop=True)
        self._editing_index = None
        self._editing_key = None
        self._selected_wire_key = None
        self._save_table()
        self._clear_form()
        self.log("Strain: removed selected entry.")

    def _reload_references_clicked(self) -> None:
        before = self._available_wire_count()
        self._load_reference_data()
        if hasattr(self, "composition_combo"):
            self._update_composition_suggestions()
        after = self._available_wire_count()
        self.log(f"Strain: reloaded reference data ({after} microwire option(s) available).")
        self._update_status()

    def _export_to_excel(self) -> None:
        if self.data.table.empty:
            QtWidgets.QMessageBox.information(
                self,
                self.section_title,
                "No strain entries to export.",
            )
            return
        suggested = Path.cwd() / "strain_data.xlsx"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export strain worksheet",
            str(suggested),
            "Excel Workbook (*.xlsx)",
        )
        if not path:
            return
        export_path = Path(path)
        if export_path.suffix.lower() != ".xlsx":
            export_path = export_path.with_suffix(".xlsx")
        frame = self.data.table[
            [
                self.COLUMN_COMPOSITION,
                self.COLUMN_MICROWIRE,
                self.COLUMN_D,
                self.COLUMN_MASS,
                self.COLUMN_M_LENGTH,
                self.COLUMN_A_LENGTH,
                self.COLUMN_STRAIN,
            ]
        ].copy()
        try:
            frame.to_excel(export_path, index=False)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                self.section_title,
                f"Failed to export worksheet:\n{exc}",
            )
            return
        self.log(f"Strain: exported worksheet to {export_path}")

    def _update_composition_suggestions(self) -> None:
        if not hasattr(self, "composition_combo"):
            return
        current_text = self.composition_combo.currentText().strip()
        available: List[str] = []
        used = self._used_wire_keys()
        if self._editing_key in used:
            used.discard(self._editing_key)
        for composition, wires in self._wire_choices.items():
            if any((composition, draw, piece) not in used for draw, piece in wires.values()):
                available.append(composition)
        available.sort(key=lambda value: value.lower())
        was_blocked = self.composition_combo.blockSignals(True)
        self.composition_combo.clear()
        for composition in available:
            self.composition_combo.addItem(composition)
        self.composition_combo.setEditText(current_text)
        self.composition_combo.blockSignals(was_blocked)
        completer = self.composition_combo.completer()
        if completer is not None:
            completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
            completer.setModel(QtCore.QStringListModel(available, completer))
        self._update_microwire_options()

    def _update_microwire_options(self) -> None:
        if not hasattr(self, "microwire_combo"):
            return
        composition = self.composition_combo.currentText().strip()
        was_blocked = self.microwire_combo.blockSignals(True)
        current_label = self.microwire_combo.currentText().strip()
        self.microwire_combo.clear()
        used = self._used_wire_keys()
        if self._editing_key in used:
            used.discard(self._editing_key)
        options = []
        for label, key in self._wire_choices.get(composition, {}).items():
            if (composition, key[0], key[1]) in used:
                continue
            options.append((label, key))
        options.sort(key=lambda item: (item[1][0], item[1][1], item[0]))
        for label, key in options:
            self.microwire_combo.addItem(label, key)
        if self._editing_key and self._editing_key[0] == composition:
            draw, piece = self._editing_key[1:]
            label = _microwire_label(draw, piece)
            if label and self.microwire_combo.findText(label) == -1:
                self.microwire_combo.insertItem(0, label, (draw, piece))
        if current_label:
            idx = self.microwire_combo.findText(current_label)
            if idx >= 0:
                self.microwire_combo.setCurrentIndex(idx)
        if self.microwire_combo.count() > 0 and self.microwire_combo.currentIndex() < 0:
            self.microwire_combo.setCurrentIndex(0)
        self.microwire_combo.blockSignals(was_blocked)
        self._microwire_changed()

    def _refresh_table_view(self) -> None:
        self.model.set_frame(self.data.table)
        if isinstance(self.table_view, QtWidgets.QTableView):
            self._update_hidden_columns()
        self._auto_fit_columns()

    def _update_hidden_columns(self) -> None:
        if not isinstance(self.table_view, QtWidgets.QTableView):
            return
        columns = list(self.data.table.columns)
        for name in self.HIDDEN_COLUMNS:
            if name in columns:
                index = columns.index(name)
                self.table_view.setColumnHidden(index, True)

    def _available_wire_count(self) -> int:
        count = 0
        used = self._used_wire_keys()
        for composition, wires in self._wire_choices.items():
            for draw, piece in wires.values():
                if (composition, draw, piece) not in used:
                    count += 1
        return count

    def _used_wire_keys(self) -> set[tuple[str, int, int]]:
        keys: set[tuple[str, int, int]] = set()
        frame = self.data.table if isinstance(self.data.table, pd.DataFrame) else pd.DataFrame()
        if frame.empty:
            return keys
        for _, row in frame.iterrows():
            composition = str(row.get(self.COLUMN_COMPOSITION) or "").strip()
            if not composition:
                continue
            draw = row.get(self.COLUMN_DRAW)
            piece = row.get(self.COLUMN_PIECE)
            if pd.notna(draw) and pd.notna(piece):
                try:
                    draw_int = int(float(draw))
                    piece_int = int(float(piece))
                except (TypeError, ValueError):
                    continue
                keys.add((composition, draw_int, piece_int))
                continue
            parsed = _microwire_tuple_from_label(str(row.get(self.COLUMN_MICROWIRE) or ""))
            if parsed:
                keys.add((composition, int(parsed[0]), int(parsed[1])))
        return keys

    def _select_key(self, key: tuple[str, int, int]) -> None:
        if not isinstance(self.table_view, QtWidgets.QTableView):
            return
        index = self._find_row_by_key(key)
        if index is None:
            return
        selection_model = self.table_view.selectionModel()
        if selection_model is None:
            return
        model_index = self.model.index(index, 0)
        selection_model.select(
            model_index,
            QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QtCore.QItemSelectionModel.SelectionFlag.Rows,
        )
        self.table_view.scrollTo(model_index, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)

    def _find_row_by_key(self, key: tuple[str, int, int]) -> Optional[int]:
        if not isinstance(self.data.table, pd.DataFrame) or self.data.table.empty:
            return None
        composition, draw, piece = key
        for index, row in self.data.table.iterrows():
            row_comp = str(row.get(self.COLUMN_COMPOSITION) or "").strip()
            if row_comp != composition:
                continue
            row_draw = row.get(self.COLUMN_DRAW)
            row_piece = row.get(self.COLUMN_PIECE)
            try:
                row_draw_int = int(float(row_draw))
                row_piece_int = int(float(row_piece))
            except (TypeError, ValueError):
                continue
            if row_draw_int == draw and row_piece_int == piece:
                return index
        return None

    def _load_reference_data(self) -> None:
        wire_choices: Dict[str, Dict[str, tuple[int, int]]] = {}
        try:
            annealing_store = MiniDatabaseStore("annealing")
            records = annealing_store.load_payload("annealing_records")
        except Exception:
            records = None
        if isinstance(records, list):
            for record in records:
                metadata = getattr(record, "metadata", None)
                if metadata is None:
                    continue
                composition = getattr(metadata, "composition_token", None)
                draw = getattr(metadata, "draw_x", None)
                piece = getattr(metadata, "piece_y", None)
                if not composition or draw is None or piece is None:
                    continue
                label = _microwire_label(int(draw), int(piece))
                bucket = wire_choices.setdefault(composition, {})
                bucket[label] = (int(draw), int(piece))
        self._wire_choices = {
            composition: dict(
                sorted(bucket.items(), key=lambda item: (item[1][0], item[1][1], item[0]))
            )
            for composition, bucket in wire_choices.items()
        }

        d_lookup: Dict[tuple[str, int, int], float] = {}
        try:
            microscope_data = MiniDatabaseStore("microscope").load()
            frame = microscope_data.table if isinstance(microscope_data.table, pd.DataFrame) else pd.DataFrame()
        except Exception:
            frame = pd.DataFrame()
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            for _, row in frame.iterrows():
                composition = str(row.get("Composition") or "").strip()
                draw = row.get("Draw")
                piece = row.get("Piece")
                if not composition:
                    continue
                try:
                    draw_int = int(float(draw))
                    piece_int = int(float(piece))
                except (TypeError, ValueError):
                    continue
                d_value = _parse_strain_float(row.get("d (µm)"))
                if d_value is None:
                    continue
                d_lookup[(composition, draw_int, piece_int)] = d_value
        self._d_lookup = d_lookup

    def _ensure_table_structure(self) -> None:
        frame = self.data.table
        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame(columns=self.TABLE_COLUMNS)
        else:
            frame = frame.copy()
            legacy_map = {
                "Strain (%)": self.COLUMN_STRAIN,
            }
            for old, new in legacy_map.items():
                if old in frame.columns and new not in frame.columns:
                    frame[new] = frame[old]
            for column in self.TABLE_COLUMNS:
                if column not in frame.columns:
                    frame[column] = pd.Series([None] * len(frame))
            frame = frame[self.TABLE_COLUMNS]
        self.data.table = frame.reset_index(drop=True)
        self._migrate_rows()

    def _migrate_rows(self) -> None:
        frame = self.data.table
        if frame.empty:
            return
        for index, row in frame.iterrows():
            microwire = str(row.get(self.COLUMN_MICROWIRE) or "").strip()
            draw = row.get(self.COLUMN_DRAW)
            piece = row.get(self.COLUMN_PIECE)
            if microwire and (pd.isna(draw) or pd.isna(piece)):
                parsed = _microwire_tuple_from_label(microwire)
                if parsed:
                    frame.at[index, self.COLUMN_DRAW] = int(parsed[0])
                    frame.at[index, self.COLUMN_PIECE] = int(parsed[1])
            a_value = row.get(self.COLUMN_A_LENGTH)
            broke = bool(row.get(self.COLUMN_BROKE))
            if isinstance(a_value, str) and a_value.strip() in {"-", "broke"}:
                frame.at[index, self.COLUMN_A_LENGTH] = "-"
                frame.at[index, self.COLUMN_BROKE] = True
            elif broke and a_value != "-":
                frame.at[index, self.COLUMN_A_LENGTH] = "-"
        self._recompute_table_metrics()

    def _recompute_table_metrics(self) -> None:
        frame = self.data.table
        for index, row in frame.iterrows():
            d_value = _parse_strain_float(row.get(self.COLUMN_D))
            mass = self._calculate_mass(d_value)
            frame.at[index, self.COLUMN_MASS] = None if mass is None else round(mass, 6)
            broke = bool(row.get(self.COLUMN_BROKE))
            a_value = row.get(self.COLUMN_A_LENGTH)
            if isinstance(a_value, str) and a_value.strip() in {"-", "broke"}:
                broke = True
                frame.at[index, self.COLUMN_A_LENGTH] = "-"
            if broke:
                frame.at[index, self.COLUMN_BROKE] = True
                frame.at[index, self.COLUMN_STRAIN] = "broke"
                continue
            frame.at[index, self.COLUMN_BROKE] = False
            m_length = _parse_strain_float(row.get(self.COLUMN_M_LENGTH))
            a_length = _parse_strain_float(a_value)
            if m_length in (None, 0) or a_length is None:
                frame.at[index, self.COLUMN_STRAIN] = None
                continue
            percent = self._compute_strain_percent(m_length, a_length)
            frame.at[index, self.COLUMN_STRAIN] = None if percent is None else round(percent, 3)

    def _save_table(self) -> None:
        self._recompute_table_metrics()
        frame = self.data.table[self.TABLE_COLUMNS].copy()
        frame.reset_index(drop=True, inplace=True)
        self.data.table = frame
        self._sync_payload()
        self.store.save(self.data)
        self._refresh_table_view()
        if hasattr(self, "composition_combo"):
            self._update_composition_suggestions()
        self._update_status()

    def _build_records_from_table(self) -> Dict[tuple[str, int, int], StrainRecord]:
        records: Dict[tuple[str, int, int], StrainRecord] = {}
        frame = self.data.table if isinstance(self.data.table, pd.DataFrame) else pd.DataFrame()
        if frame.empty:
            return records
        for _, row in frame.iterrows():
            composition = str(row.get(self.COLUMN_COMPOSITION) or "").strip()
            if not composition:
                continue
            draw = row.get(self.COLUMN_DRAW)
            piece = row.get(self.COLUMN_PIECE)
            try:
                draw_int = int(float(draw))
                piece_int = int(float(piece))
            except (TypeError, ValueError):
                continue
            label = _microwire_label(draw_int, piece_int)
            m_length = _parse_strain_float(row.get(self.COLUMN_M_LENGTH))
            a_value = row.get(self.COLUMN_A_LENGTH)
            broke = bool(row.get(self.COLUMN_BROKE))
            if isinstance(a_value, str) and a_value.strip() in {"-", "broke"}:
                broke = True
                a_length = None
            else:
                a_length = _parse_strain_float(a_value)
            strain_value = row.get(self.COLUMN_STRAIN)
            if broke:
                percent = None
            else:
                percent = _parse_strain_float(strain_value)
                if percent is None and m_length not in (None, 0) and a_length is not None:
                    percent = self._compute_strain_percent(m_length, a_length)
            records[(composition, draw_int, piece_int)] = StrainRecord(
                composition=composition,
                draw=draw_int,
                piece=piece_int,
                microwire_label=label,
                m_length=m_length,
                a_length=a_length,
                percent=percent,
                broke=broke,
                source=Path("manual_entry"),
            )
        return records

    def _sync_payload(self) -> None:
        records = self._build_records_from_table()
        payloads = self.data.extra.get("payloads")
        if not isinstance(payloads, dict):
            payloads = {}
        payloads["strain_records"] = "strain_records"
        self.data.extra["payloads"] = payloads
        self.store.save_payload("strain_records", records)

    def _strain_offset_changed(self, value: float) -> None:
        self._strain_offset = float(value)
        if not isinstance(self.data.extra, dict):
            self.data.extra = {}
        self.data.extra["strain_offset"] = self._strain_offset
        self._recompute_table_metrics()
        self._refresh_table_view()
        self._sync_payload()
        self.store.save(self.data)
        self._update_status()
        self._update_strain_display()

    def _compute_strain_percent(
        self,
        m_length: Optional[float],
        a_length: Optional[float],
    ) -> Optional[float]:
        if m_length in (None, 0) or a_length is None:
            return None
        try:
            base_ratio = (m_length - a_length) / m_length
        except ZeroDivisionError:
            return None
        try:
            offset = float(self._strain_offset)
        except (TypeError, ValueError):
            offset = 0.0
        return (base_ratio + offset) * 100

    @staticmethod
    def _calculate_mass(d_um: Optional[float]) -> Optional[float]:
        if d_um is None or d_um <= 0:
            return None
        radius_m = (d_um * 1e-6) / 2.0
        if radius_m <= 0:
            return None
        area = math.pi * radius_m * radius_m
        return area * 1.0e11 / 9.80665


class AssemblySection(QtWidgets.QWidget):
    """Final step that merges prepared mini-databases into a spreadsheet."""

    def __init__(
        self,
        sections: Dict[str, MiniDatabaseSection],
        logger: logging.Logger,
        log_callback: Callable[[int, str], None],
        console_callback: Optional[Callable[[pd.DataFrame], None]] = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.sections = sections
        self.logger = logger
        self._log_callback = log_callback
        self._console_callback = console_callback

    def set_console_callback(self, callback: Callable[[pd.DataFrame], None]) -> None:
        self._console_callback = callback

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)

        self.output_dir_edit = QtWidgets.QLineEdit(str(Path.cwd()))
        browse_button = QtWidgets.QPushButton("Browse…")
        browse_button.clicked.connect(self._choose_output_dir)
        dir_row = QtWidgets.QHBoxLayout()
        dir_row.addWidget(self.output_dir_edit)
        dir_row.addWidget(browse_button)
        dir_container = QtWidgets.QWidget()
        dir_container.setLayout(dir_row)
        form.addRow("Output directory", dir_container)

        self.output_name_edit = QtWidgets.QLineEdit(DEFAULT_OUTPUT_NAME)
        form.addRow("Output name", self.output_name_edit)

        layout.addLayout(form)

        options_layout = QtWidgets.QHBoxLayout()
        self.csv_checkbox = QtWidgets.QCheckBox("Export CSV")
        self.csv_checkbox.setChecked(True)
        options_layout.addWidget(self.csv_checkbox)
        self.excel_checkbox = QtWidgets.QCheckBox("Export Excel")
        options_layout.addWidget(self.excel_checkbox)
        self.plots_checkbox = QtWidgets.QCheckBox("Create Matplotlib plots")
        options_layout.addWidget(self.plots_checkbox)
        self.origin_checkbox = QtWidgets.QCheckBox("Export Origin workbooks")
        options_layout.addWidget(self.origin_checkbox)
        options_layout.addStretch(1)
        layout.addLayout(options_layout)

        section_box = QtWidgets.QGroupBox("Include sections")
        section_layout = QtWidgets.QHBoxLayout(section_box)
        self.section_checkboxes: Dict[str, QtWidgets.QCheckBox] = {}
        for key, label in (
            ("fabrication", "Fabrication"),
            ("annealing", "Current annealing"),
            ("microscope", "Microscope"),
            ("videos", "Videos"),
            ("strain", "Strain"),
        ):
            checkbox = QtWidgets.QCheckBox(label)
            checkbox.setChecked(True)
            section_layout.addWidget(checkbox)
            self.section_checkboxes[key] = checkbox
        section_layout.addStretch(1)
        layout.addWidget(section_box)

        self.status_label = QtWidgets.QLabel("Ready to assemble once all sections are processed.")
        layout.addWidget(self.status_label)

        self.preview_model = DataFrameModel()
        self.preview_table = QtWidgets.QTableView()
        self.preview_table.setModel(self.preview_model)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setVerticalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.preview_table.setHorizontalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        preview_bar = self.preview_table.verticalScrollBar()
        if preview_bar is not None:
            preview_bar.setSingleStep(MiniDatabaseSection._SCROLL_SINGLE_STEP)
        layout.addWidget(self.preview_table, 1)
        self.preview_table.show()

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        self.preview_button = QtWidgets.QPushButton("Preview database")
        self.preview_button.clicked.connect(self._preview)
        button_row.addWidget(self.preview_button)
        self.combine_button = QtWidgets.QPushButton("Combine database")
        self.combine_button.clicked.connect(self._combine)
        button_row.addWidget(self.combine_button)
        layout.addLayout(button_row)

    def log(self, message: str, level: int = logging.INFO) -> None:
        try:
            self._log_callback(level, message)
        except Exception:
            self.logger.log(level, message)

    def _choose_output_dir(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory")
        if directory:
            self.output_dir_edit.setText(directory)

    def _load_payload(self, section_key: str, name: str) -> Any:
        section = self.sections.get(section_key)
        if section is None:
            return None
        return section.store.load_payload(name)

    def _selected_sections(self) -> set[str]:
        return {
            key
            for key, checkbox in self.section_checkboxes.items()
            if checkbox.isChecked()
        }

    def _prepare_builder_inputs(
        self,
        selected: set[str],
        *,
        require_payloads: bool = True,
    ) -> Optional[
        tuple[
            FabricationIndex,
            List[MeasurementRecord],
            Dict[Tuple[str, int, int], MicroscopeMeasurements],
            Dict[Tuple[str, int, Optional[int]], VideoMetricsSummary],
            Dict[Tuple[str, int, int], StrainRecord],
            Dict[str, Dict[str, float]],
        ]
    ]:
        if "annealing" not in selected:
            QtWidgets.QMessageBox.warning(
                self,
                "Microwire Data Builder",
                "Current annealing data must be included to assemble a database.",
            )
            return None

        missing: list[str] = []

        fabrication_index = FabricationIndex()
        if "fabrication" in selected:
            payload = self._load_payload("fabrication", "fabrication_index")
            if isinstance(payload, FabricationIndex):
                fabrication_index = payload
            elif require_payloads:
                missing.append("fabrication")

        annealing_records_payload = self._load_payload("annealing", "annealing_records")
        annealing_records: List[MeasurementRecord] = []
        if isinstance(annealing_records_payload, list):
            annealing_records = list(annealing_records_payload)
        if require_payloads and not annealing_records:
            missing.append("annealing")

        microscope_index: Dict[Tuple[str, int, int], MicroscopeMeasurements] = {}
        overrides: Dict[str, Dict[str, float]] = {}
        if "microscope" in selected:
            payload = self._load_payload("microscope", "microscope_index")
            if isinstance(payload, dict):
                microscope_index = payload
            elif require_payloads:
                missing.append("microscope")
            microscope_section = self.sections.get("microscope")
            if isinstance(microscope_section, MicroscopeSection):
                overrides = microscope_section.overrides

        video_index: Dict[Tuple[str, int, Optional[int]], VideoMetricsSummary] = {}
        if "videos" in selected:
            payload = self._load_payload("videos", "video_index")
            if isinstance(payload, dict):
                video_index = payload
            elif require_payloads:
                missing.append("videos")

        strain_records: Dict[Tuple[str, int, int], StrainRecord] = {}
        if "strain" in selected:
            payload = self._load_payload("strain", "strain_records")
            if isinstance(payload, dict):
                strain_records = payload
            elif require_payloads:
                missing.append("strain")

        if missing:
            QtWidgets.QMessageBox.warning(
                self,
                "Microwire Data Builder",
                "Process the following sections first: " + ", ".join(sorted(missing)),
            )
            return None

        return (
            fabrication_index,
            annealing_records,
            microscope_index,
            video_index,
            strain_records,
            overrides,
        )

    def _update_preview(self, frame: pd.DataFrame) -> None:
        self.preview_model.set_frame(frame)
        self.preview_table.setVisible(True)
        try:
            self.preview_table.resizeColumnsToContents()
        except Exception:
            pass
        if self._console_callback is not None and isinstance(frame, pd.DataFrame):
            try:
                self._console_callback(frame.copy())
            except Exception:
                self.logger.exception("Failed to publish preview dataframe to console")
        row_count = len(frame.index) if isinstance(frame, pd.DataFrame) else 0
        if row_count:
            self.status_label.setText(f"Preview ready — {row_count} row(s).")
        else:
            self.status_label.setText("Preview is empty.")

    def _combine(self) -> None:
        selected = self._selected_sections()
        inputs = self._prepare_builder_inputs(selected)
        if inputs is None:
            return

        (
            fabrication_index,
            annealing_records,
            microscope_index,
            video_index,
            strain_records,
            overrides,
        ) = inputs

        if "microscope" in selected and overrides:
            microscope_index = _apply_microscope_overrides(microscope_index, overrides)
        else:
            microscope_index = {} if "microscope" not in selected else microscope_index

        output_dir = Path(self.output_dir_edit.text().strip() or Path.cwd())
        output_dir.mkdir(parents=True, exist_ok=True)
        output_name = _normalise_output_name(self.output_name_edit.text() or DEFAULT_OUTPUT_NAME)

        formats: List[str] = []
        if self.csv_checkbox.isChecked():
            formats.append("csv")
        if self.excel_checkbox.isChecked():
            formats.append("excel")
        backends: List[str] = []
        if self.plots_checkbox.isChecked():
            backends.append("matplotlib")
        if self.origin_checkbox.isChecked():
            backends.append("origin")

        config = BuilderConfig(
            annealing_files=[],
            fabrication_files=[],
            output_dir=output_dir,
            microscope_files=[],
            video_files=[],
            strain_files=[],
            make_plots=self.plots_checkbox.isChecked(),
            export_formats=tuple(formats) if formats else ("csv",),
            plot_backends=tuple(backends),
            output_name=output_name,
        )

        try:
            result = build_database(
                config,
                logger=self.logger,
                fabrication_index=fabrication_index,
                measurement_records=annealing_records,
                microscope_index=microscope_index if "microscope" in selected else {},
                video_index=video_index if "videos" in selected else {},
                strain_records=strain_records if "strain" in selected else {},
            )
        except Exception as exc:
            self.logger.exception("Assembly failed")
            QtWidgets.QMessageBox.critical(
                self,
                "Microwire Data Builder",
                f"Failed to assemble database:\n{exc}",
            )
            return

        exports_text = ", ".join(f"{fmt.upper()}: {path}" for fmt, path in result.exports.items())
        if not exports_text:
            exports_text = "No exports generated."
        QtWidgets.QMessageBox.information(
            self,
            "Microwire Data Builder",
            f"Database assembled successfully.\n{exports_text}",
        )
        self.log("Database combined successfully.")
        self._update_preview(result.dataframe)

    def _preview(self) -> None:
        selected = self._selected_sections()
        inputs = self._prepare_builder_inputs(selected)
        if inputs is None:
            return

        (
            fabrication_index,
            annealing_records,
            microscope_index,
            video_index,
            strain_records,
            overrides,
        ) = inputs

        if "microscope" in selected and overrides:
            microscope_index = _apply_microscope_overrides(microscope_index, overrides)
        else:
            microscope_index = {} if "microscope" not in selected else microscope_index

        output_dir = Path(self.output_dir_edit.text().strip() or Path.cwd())
        output_dir.mkdir(parents=True, exist_ok=True)
        output_name = _normalise_output_name(self.output_name_edit.text() or DEFAULT_OUTPUT_NAME)

        config = BuilderConfig(
            annealing_files=[],
            fabrication_files=[],
            output_dir=output_dir,
            microscope_files=[],
            video_files=[],
            strain_files=[],
            make_plots=False,
            export_formats=(),
            plot_backends=(),
            output_name=output_name,
        )

        try:
            result = build_database(
                config,
                logger=self.logger,
                fabrication_index=fabrication_index,
                measurement_records=annealing_records,
                microscope_index=microscope_index if "microscope" in selected else {},
                video_index=video_index if "videos" in selected else {},
                strain_records=strain_records if "strain" in selected else {},
                skip_exports=True,
            )
        except Exception as exc:
            self.logger.exception("Preview failed")
            QtWidgets.QMessageBox.critical(
                self,
                "Microwire Data Builder",
                f"Failed to preview database:\n{exc}",
            )
            return

        self._update_preview(result.dataframe)
        self.log("Preview updated.")


class BuilderWindow(QtWidgets.QMainWindow):
    """New workbench for preparing and assembling microwire databases."""

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(LOGGER_NAME)
        self.setWindowTitle("Microwire Data Builder")
        self.resize(1100, 720)

        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tab_widget = QtWidgets.QTabWidget(central)
        layout.addWidget(self.tab_widget, 1)

        self.setCentralWidget(central)

        self._primary_dock_widths: Dict[QtWidgets.QDockWidget, int] = {}
        self._retabbing_docks = False
        self._retabify_pending = False

        self.log_view = QtWidgets.QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setObjectName("builderMessageLogView")

        self.sections: Dict[str, MiniDatabaseSection] = {}
        self._log_has_unread_errors = False
        self._log_highlight_active = False

        def _append_log(level: int, message: str) -> None:
            self.log_view.appendPlainText(message)
            scrollbar = self.log_view.verticalScrollBar()
            if scrollbar is not None:
                scrollbar.setValue(scrollbar.maximum())
            if level >= logging.ERROR:
                dock = getattr(self, "log_dock", None)
                if isinstance(dock, QtWidgets.QDockWidget) and dock.isVisible() and self.isActiveWindow():
                    self._log_has_unread_errors = False
                else:
                    self._log_has_unread_errors = True
            self._update_log_highlight()

        self._log_handler = QtLogHandler(_append_log)
        self._log_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        if self._log_handler not in self.logger.handlers:
            self.logger.addHandler(self._log_handler)

        self.annealing_section = AnnealingSection(self.logger, _append_log)
        self.tab_widget.addTab(self.annealing_section, "Current annealing")
        self.sections["annealing"] = self.annealing_section

        self.fabrication_section = FabricationSection(self.logger, _append_log)
        self.tab_widget.addTab(self.fabrication_section, "Fabrication")
        self.sections["fabrication"] = self.fabrication_section

        self.microscope_section = MicroscopeSection(self.logger, _append_log)
        self.tab_widget.addTab(self.microscope_section, "Microscope")
        self.sections["microscope"] = self.microscope_section

        self.video_section = VideoSection(self.logger, _append_log)
        self.tab_widget.addTab(self.video_section, "Videos")
        self.sections["videos"] = self.video_section

        self._developer_options = developer_options()
        try:
            self._developer_options.ocr_debug_changed.connect(
                self._handle_ocr_debug_changed
            )
        except Exception:
            pass
        self._handle_ocr_debug_changed(self._developer_options.ocr_debug())

        self.strain_section = StrainSection(self.logger, _append_log)
        self.tab_widget.addTab(self.strain_section, "Strain")
        self.sections["strain"] = self.strain_section

        assembly = AssemblySection(
            self.sections,
            self.logger,
            _append_log,
            console_callback=self._display_dataframe_in_console,
        )
        self.assembly_section = assembly
        self.tab_widget.addTab(assembly, "Assemble")

        self.fabrication_section.sources_changed.connect(
            self._handle_fabrication_sources_changed
        )
        self._handle_fabrication_sources_changed(self.fabrication_section.data.sources)

        self.project_tree = QtWidgets.QTreeWidget()
        self.project_tree.setHeaderLabels(["Section", "Status / Source"])
        self.project_tree.header().setStretchLastSection(True)
        self._project_items: Dict[str, QtWidgets.QTreeWidgetItem] = {}
        for key, section in self.sections.items():
            status_text = section.status_label.text() if hasattr(section, "status_label") else ""
            item = QtWidgets.QTreeWidgetItem([section.section_title, status_text])
            self.project_tree.addTopLevelItem(item)
            self._project_items[key] = item
            section.status_changed.connect(partial(self._handle_section_status_changed, key))
            section.sources_changed.connect(partial(self._handle_section_sources_changed, key))
            self._handle_section_sources_changed(key, section.data.sources)

        self.project_dock = QtWidgets.QDockWidget("Project Explorer", self)
        self.project_dock.setObjectName("builderProjectExplorerDock")
        self.project_dock.setWidget(self.project_tree)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)

        self.log_dock = QtWidgets.QDockWidget("Message Log", self)
        self.log_dock.setObjectName("builderMessageLogDock")
        self.log_dock.setWidget(self.log_view)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, self.log_dock)
        self.log_dock.visibilityChanged.connect(self._handle_log_visibility)
        self.log_dock.hide()
        self._update_log_highlight()

        for dock in (self.project_dock, self.log_dock):
            if dock is None:
                continue
            dock.setAllowedAreas(QtCore.Qt.DockWidgetArea.AllDockWidgetAreas)
            dock.setFeatures(
                QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
                | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable
            )
            dock.hide()

        self.console_widget = PythonConsoleWidget(self)
        self.console_widget.set_environment({"window": self, "pd": pd})
        self.console_dock = QtWidgets.QDockWidget("Python Console", self)
        self.console_dock.setObjectName("builderPythonConsoleDock")
        self.console_dock.setWidget(self.console_widget)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self.console_dock)
        self.console_dock.hide()

        self._dock_switcher_panels: List[QtWidgets.QDockWidget | None] = []
        if self._dock_switcher_supported():
            self._dock_switcher_panels.append(
                self._create_dock_switcher(
                    [self.project_dock, self.log_dock],
                    side="left",
                    initial_visible=(),
                )
            )
        else:
            self._dock_switcher_panels.append(None)

        for dock in (self.project_dock, self.log_dock):
            if dock is None:
                continue
            try:
                dock.dockLocationChanged.connect(
                    lambda area, d=dock: self._handle_primary_dock_location_change(d, area)
                )
            except Exception:
                pass
            try:
                dock.visibilityChanged.connect(
                    lambda _visible, d=dock: self._handle_primary_dock_visibility_changed(d)
                )
            except Exception:
                pass

        install_standard_menu(self, help_topic="builder_database", console=self.log_view)
        self.setWindowState(self.windowState() | QtCore.Qt.WindowState.WindowMaximized)
        self._retabify_primary_docks()

    def _dock_switcher_supported(self) -> bool:
        override = os.environ.get("MW_DISABLE_DOCK_SWITCHER", "")
        return override.strip().lower() not in {"1", "true", "yes", "on"}

    def _create_dock_switcher(
        self,
        docks: Sequence[QtWidgets.QDockWidget],
        *,
        side: str,
        initial_visible: Iterable[int] | None = None,
    ) -> QtWidgets.QDockWidget | None:
        if not docks:
            return None
        panel = QtWidgets.QDockWidget("", self)
        panel.setObjectName(f"builder_{side}_dock_switcher")
        panel.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            if side == "left"
            else QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        )
        panel.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        panel.setTitleBarWidget(QtWidgets.QWidget(panel))

        switcher = _DockSwitcherWidget(docks, side=side, parent=panel)
        if initial_visible is not None:
            try:
                switcher.set_initial_visible(tuple(initial_visible))
            except Exception:
                pass
        panel.setWidget(switcher)
        width = switcher.sizeHint().width()
        panel.setMinimumWidth(width)
        panel.setMaximumWidth(width)

        area = (
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            if side == "left"
            else QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(area, panel)
        reference = docks[0]
        try:
            self.splitDockWidget(panel, reference, QtCore.Qt.Orientation.Horizontal)
        except Exception:
            pass
        return panel

    def _handle_log_visibility(self, visible: bool) -> None:
        if visible and getattr(self, "_log_has_unread_errors", False):
            self._log_has_unread_errors = False
            self._update_log_highlight()

    def _update_log_highlight(self) -> None:
        dock = getattr(self, "log_dock", None)
        if not isinstance(dock, QtWidgets.QDockWidget):
            return
        highlight = bool(getattr(self, "_log_has_unread_errors", False))
        if getattr(self, "_log_highlight_active", False) == highlight:
            return
        self._log_highlight_active = highlight
        if highlight:
            dock.setStyleSheet(
                "QDockWidget#builderMessageLogDock { border: 1px solid #c62828; }"
            )
            self.log_view.setStyleSheet("background-color: #ffebee;")
        else:
            dock.setStyleSheet("")
            self.log_view.setStyleSheet("")

    def _handle_fabrication_sources_changed(self, sources: Iterable[str]) -> None:
        video = getattr(self, "video_section", None)
        if isinstance(video, MiniDatabaseSection):
            video.set_sources(sources)

    def _collect_primary_docks(self) -> List[QtWidgets.QDockWidget]:
        docks: List[QtWidgets.QDockWidget] = []
        for candidate in (getattr(self, "project_dock", None), getattr(self, "log_dock", None)):
            if isinstance(candidate, QtWidgets.QDockWidget):
                docks.append(candidate)
        return docks

    def _handle_primary_dock_location_change(
        self,
        dock: QtWidgets.QDockWidget,
        area: QtCore.Qt.DockWidgetArea,
    ) -> None:
        if getattr(self, "_retabbing_docks", False):
            return
        if area == QtCore.Qt.DockWidgetArea.LeftDockWidgetArea:
            self._queue_retabify_primary_docks()

    def _handle_primary_dock_visibility_changed(self, dock: QtWidgets.QDockWidget) -> None:
        if getattr(self, "_retabbing_docks", False):
            return
        _ = dock
        self._queue_retabify_primary_docks()

    def _queue_retabify_primary_docks(self) -> None:
        if getattr(self, "_retabify_pending", False):
            return
        self._retabify_pending = True
        QtCore.QTimer.singleShot(0, self._run_queued_retabify)

    def _run_queued_retabify(self) -> None:
        self._retabify_pending = False
        self._retabify_primary_docks()

    def _retabify_primary_docks(self) -> None:
        if getattr(self, "_retabbing_docks", False):
            return
        self._retabbing_docks = True
        try:
            docks = [dock for dock in self._collect_primary_docks() if not dock.isFloating()]
            if not docks:
                return
            primary = docks[0]
            for dock in docks[1:]:
                try:
                    self.tabifyDockWidget(primary, dock)
                except Exception:
                    continue
            for dock in docks:
                width = max(dock.width(), self._primary_dock_widths.get(dock, 240))
                self._apply_dock_width(dock, width)
        finally:
            self._retabbing_docks = False

    def _apply_dock_width(self, dock: QtWidgets.QDockWidget, width: int) -> None:
        if dock.isFloating() or width <= 0:
            return
        try:
            dock.resize(width, dock.height())
            self._primary_dock_widths[dock] = width
        except Exception:
            pass

    def _handle_ocr_debug_changed(self, enabled: bool) -> None:
        section = getattr(self, "microscope_section", None)
        if isinstance(section, MicroscopeSection):
            try:
                section.set_ocr_debug_enabled(bool(enabled))
            except Exception:
                pass

    def _handle_section_status_changed(self, key: str, status: str) -> None:
        item = self._project_items.get(key)
        if item is not None:
            item.setText(1, status)

    def _handle_section_sources_changed(self, key: str, sources: Iterable[str]) -> None:
        item = self._project_items.get(key)
        if item is None:
            return
        item.takeChildren()
        section = self.sections.get(key)
        processed: Dict[str, float] = {}
        if isinstance(section, MiniDatabaseSection):
            processed = dict(getattr(section.data, "processed", {}))
        for source in sources:
            source_text = str(source)
            child = QtWidgets.QTreeWidgetItem(["", source_text])
            child.setToolTip(1, source_text)
            source_path = Path(source_text)
            related_files: List[str] = []
            for path_text in sorted(processed.keys()):
                try:
                    path_obj = Path(path_text)
                except Exception:
                    continue
                try:
                    rel = path_obj.relative_to(source_path)
                    related_files.append(str(rel))
                except Exception:
                    if path_text.startswith(source_text):
                        related_files.append(path_text[len(source_text) :].lstrip(os.sep))
            related_files = list(dict.fromkeys(related_files))
            for rel_path in related_files:
                if not rel_path:
                    continue
                leaf = QtWidgets.QTreeWidgetItem(["", rel_path])
                leaf.setToolTip(1, rel_path)
                child.addChild(leaf)
            item.addChild(child)
        if sources:
            item.setExpanded(False)

    def _display_dataframe_in_console(self, frame: pd.DataFrame) -> None:
        if not isinstance(frame, pd.DataFrame):
            return
        try:
            self.console_widget.set_environment({"database": frame})
            rows = len(frame.index)
            cols = len(frame.columns)
            summary = f"database -> DataFrame with {rows} row(s) × {cols} column(s)"
            self.console_widget.output.appendPlainText(summary)
        except Exception:
            self.logger.exception("Failed to stream dataframe to console")
            return
        self.console_dock.show()
        try:
            self.console_dock.raise_()
        except Exception:
            pass

def run_app() -> None:
    main()


def main() -> QtWidgets.QWidget | None:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        ensure_app_theme(app)
        owns_app = True
    window = BuilderWindow()
    window.show()
    if owns_app:
        app.exec()
    return window


__all__ = ["BuilderWindow", "main", "run_app"]









