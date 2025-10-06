"""PyQt6 user interface for the microwire database builder."""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from PyQt6 import QtCore, QtGui, QtWidgets
from plotting.utils import ensure_app_theme, install_standard_menu

from .core import (
    LOGGER_NAME,
    DEFAULT_FIGSIZE,
    DEFAULT_OUTPUT_NAME,
    BuildResult,
    BuilderConfig,
    BuildCancelledError,
    build_database,
    _normalise_output_name,
    _metadata_from_path,
    _microscope_key,
    _draw_key,
)


MICROSCOPE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov")


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
) -> tuple[list[Path], list[Path], list[Path]]:
    """Locate fabrication spreadsheets, microscope images and videos.

    When ``progress_callback`` is supplied it is invoked after each annealing
    file has been analysed so callers can surface responsive progress updates
    while the filesystem scan is running.
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


class QtLogHandler(logging.Handler):
    """Logging handler that forwards records to a Qt slot."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        super().__init__()
        self._emit = emit

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - thin wrapper
        message = self.format(record)
        self._emit(message)


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
            )
            _check_cancelled()
            self._tracker.finish_prepare()
            _check_cancelled()
            microscope_files = list(dict.fromkeys(manual_microscope + auto_microscope))
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


class BuilderWindow(QtWidgets.QMainWindow):
    """Main window that orchestrates the microwire database build."""

    log_message = QtCore.pyqtSignal(str)

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
        install_standard_menu(self, help_topic="builder_database", console=self.log_group)

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
        if not self._progress_counts_text:
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

        label_text = self._progress_counts_text
        if eta_text:
            label_text += f" • {eta_text}"
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
        return collect_support_files(annealing_files, self.data_roots)

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
            self.progress_label.setText("Preparing...")
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
        for key in self._stage_order:
            total_units = max(int(self._stage_totals.get(key, 0)), 0)
            progress_units = min(max(int(self._stage_progress.get(key, 0)), 0), total_units)
            if total_units > 0 and progress_units < total_units:
                active_stage = key
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
    def _append_log(self, message: str) -> None:
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









