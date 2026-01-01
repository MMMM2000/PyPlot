"""Core data processing for the microwire database builder."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import sys
import logging
import math
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, cast
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
import pandas as pd

try:
    from .video import extract_video_metrics
except ImportError:
    module_name = "microwire_data_builder.video"
    module_path = Path(__file__).with_name("video.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        extract_video_metrics = module.extract_video_metrics
    else:
        raise

try:
    from .manual_diameters import MANUAL_DIAMETER_OVERRIDES
except ImportError:
    module_name = "microwire_data_builder.manual_diameters"
    module_path = Path(__file__).with_name("manual_diameters.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        MANUAL_DIAMETER_OVERRIDES = module.MANUAL_DIAMETER_OVERRIDES
    else:
        raise

try:
    from .ocr import get_paddle_ocr
except ImportError:
    module_name = "microwire_data_builder.ocr"
    module_path = Path(__file__).with_name("ocr.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        get_paddle_ocr = module.get_paddle_ocr
    else:
        raise

sys.modules.setdefault("microwire_data_builder.core", sys.modules[__name__])

os.environ.setdefault("MPLBACKEND", "Agg")

LOGGER_NAME = "microwire_data_builder"
R_CHECK_THRESHOLD = 0.05
DEFAULT_OUTPUT_NAME = "microwire_database"
PLOT_DIR_NAME = "plots"
ORIGIN_DIR_NAME = "origin_objects"


class BuildCancelledError(Exception):
    """Raised when a build is cancelled by the caller."""

MICRO_SIGN = "µ"
MICROSCOPE_RESIZE_TARGET = 2200
FOCUS_ROI_LIMIT = 3

OUTPUT_COLUMNS = [
    "Composition",
    "Microwire",
    "d (µm)",
    "D (µm)",
    "d/D",
    "Figure — 1000 mA",
    "Figure — low mA",
    "Strain",
    "As (mA)",
    "Ms (mA)",
    "Length (m)",
    "Production datetime",
    "Mass (g)",
    "Resistance (Ω)",
    "Temperature (°C)",
    "Winding speed (m/min)",
    "Glass feeding (mm/min)",
    "Underpressure",
    "Notes",
    "Figure — 1000 mA (Origin)",
    "Figure — low mA (Origin)",
    "Low mA value (mA)",
    "File 1000 mA",
    "File low mA",
]

DIAMETER_COLUMN = OUTPUT_COLUMNS[2]
GLASS_DIAMETER_COLUMN = OUTPUT_COLUMNS[3]
DIAMETER_RATIO_COLUMN = OUTPUT_COLUMNS[4]

MICROSCOPE_IMAGE_COLUMNS = (
    "d (µm) image",
    "D (µm) image",
)

FIGURE_COLUMNS = (
    "Figure — 1000 mA",
    "Figure — low mA",
)

STRAIN_COLUMN = "Strain"

ORIGIN_FIGURE_COLUMNS = tuple(
    column
    for column in OUTPUT_COLUMNS
    if column.startswith("Figure") and "(Origin)" in column
)

MICROWIRE_LABEL_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

_INVALID_FILENAME_CHARS = set('<>:"/\\|?*')

DRAW_NUMERIC_FIELDS = {
    "mass_g",
    "fabrication_resistance_ohm",
    "winding_speed_m_per_min",
    "glass_feed_mm_per_min",
    "underpressure",
}
PIECE_NUMERIC_FIELDS = {
    "piece_turns",
    "length_m",
    "d_um",
    "D_um",
    "d_over_D",
}
DIMENSION_FIELDS = {"d_um", "D_um", "d_over_D"}
DATETIME_FIELDS = {"production_datetime"}
DATE_FIELDS = {"piece_date"}
RAW_VALUE_FIELDS = (
    DRAW_NUMERIC_FIELDS
    | PIECE_NUMERIC_FIELDS
    | DATETIME_FIELDS
    | DATE_FIELDS
    | {"fabrication_temperature_c"}
)

HEADER_HINTS: Dict[str, str] = {
    "Dtum": "piece_date",
    "dtum": "piece_date",
    "Poet otok": "piece_turns",
    "P.": "piece_y",
    "zloÅ¾enie": "composition_label",
    "zlozenie": "composition_label",
    "composition": "composition_label",
    "dÃ¡tum a Äas vÃ½roby": "production_datetime",
    "datum a cas vyroby": "production_datetime",
    "datumacasvyroby": "production_datetime",
    "hmotnosÅ¥": "mass_g",
    "hmotnost": "mass_g",
    "mass": "mass_g",
    "odpor": "fabrication_resistance_ohm",
    "resistance": "fabrication_resistance_ohm",
    "teplota": "fabrication_temperature_c",
    "temperature": "fabrication_temperature_c",
    "winding speed (m/min)": "winding_speed_m_per_min",
    "glass feeding (mm/min)": "glass_feed_mm_per_min",
    "underpressure": "underpressure",
    "bistabilny/nebistabilny": "bistable_status",
    "poznÃ¡mka": "notes",
    "PoznÃ¡mka": "notes",
    "poznamka": "notes",
    "Poznamka": "notes",
    "pozn.": "notes",
    "pozn": "notes",
    "poznÃ¡mky": "notes",
    "p.Ä": "piece_y",
    "p.c": "piece_y",
    "p.Ä.": "piece_y",
    "poÄet otÃ¡Äok": "piece_turns",
    "pocet otacok": "piece_turns",
    "dÄºÅ¾ka (m)": "length_m",
    "dlzka (m)": "length_m",
    "d (Âµm)": "d_um",
    "d (um)": "d_um",
    "d (Î¼m)": "d_um",
    "d (μm)": "d_um",
    "d (�m)": "d_um",
    "d (µm)": "d_um",
    "d(Âµm)": "d_um",
    "d(um)": "d_um",
    "d(Î¼m)": "d_um",
    "d": "d_um",
    "D (Âµm)": "D_um",
    "D (um)": "D_um",
    "D (Î¼m)": "D_um",
    "D (μm)": "D_um",
    "D (�m)": "D_um",
    "D (µm)": "D_um",
    "D(Âµm)": "D_um",
    "D(um)": "D_um",
    "D(Î¼m)": "D_um",
    "D": "D_um",
    "d/D": "d_over_D",
    "d/d": "d_over_D",
    "Datum": "piece_date",
    "DÃ¡tum": "piece_date",
    "datum": "piece_date",
    "dÃ¡tum": "piece_date",
}

ANNEALING_COLUMNS = ["I_A", "V_V", "R_ohm"]

DRAW_PATTERN = re.compile(r"^(?P<draw>\d+)")
PIECE_PATTERN = re.compile(r"^(?P<piece>\d+)")
XY_PATTERN = re.compile(r"(\d+)_+(\d+)")
MICROSCOPE_PAIR_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"(\d+)_+(\d+)"),
    re.compile(r"(\d+)[/-](\d+)")
)
MICROSCOPE_WHITESPACE_PAIR = re.compile(r"\b(\d{1,3})\s+(\d{1,3})\b")
SETPOINT_PATTERN = re.compile(r"(\d{1,4})mA", re.IGNORECASE)
ALT_VARIANT_PATTERN = re.compile(r"(?:s\d+|\d+_\d+)a(?!\w)", re.IGNORECASE)
DOT_DATE_PATTERN = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")
SLASH_DATE_PATTERN = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")

MICROSCOPE_MARKER_PATTERN = re.compile(r"[\[{(]\s*(?P<digit>[12Il])\s*[\]})1Il]?")
MICROSCOPE_PRIMARY_HINT = re.compile(r"(\[\s*[1Il]|[1Il]\])$")
MICROSCOPE_PRIMARY_PATTERN = re.compile(
    rf"\[1]\s*(?P<value>\d+(?:[.,]\d+)?)\s*(?:u?m|{MICRO_SIGN}m)",
    re.IGNORECASE,
)
MICROSCOPE_VALUE_PATTERN = re.compile(
    rf"(?P<value>\d+(?:[.,]\d+)?)\s*(?:u?m|{MICRO_SIGN}m)",
    re.IGNORECASE,
)
MICROSCOPE_SECONDARY_PREFIX = re.compile(r"\[2]\s*$", re.IGNORECASE)
MICROSCOPE_NUMBER_TOKEN = re.compile(r"^\d+(?:[.,]\d+)?$")
MICROSCOPE_UNIT_HINTS = ("Âµm", "um", "Î¼m")

KNOWN_TIMEZONE_TOKENS = {
    "UTC",
    "GMT",
    "CET",
    "CEST",
    "EET",
    "EEST",
    "BST",
    "IST",
    "WEST",
    "WET",
    "EST",
    "EDT",
    "CST",
    "CDT",
    "MST",
    "MDT",
    "PST",
    "PDT",
}

MICROSCOPE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")


DEFAULT_FIGSIZE: Tuple[float, float] = (10.0, 6.0)


def _normalise_figsize(value: Sequence[float] | Tuple[float, float] | None) -> Tuple[float, float]:
    default = DEFAULT_FIGSIZE
    if not value:
        return default
    try:
        width, height = value  # type: ignore[misc]
    except Exception:
        return default
    try:
        width_f = float(width)
        height_f = float(height)
    except Exception:
        return default
    if not math.isfinite(width_f) or width_f <= 0:
        width_f = default[0]
    if not math.isfinite(height_f) or height_f <= 0:
        height_f = default[1]
    return (max(0.5, width_f), max(0.5, height_f))

EXCEL_EMBED_DPI = 96.0
EMU_PER_INCH = 914400


def _normalise_dpi(value: object) -> Tuple[float, float]:
    """Return ``(x, y)`` DPI extracted from ``value``.

    ``value`` may be a number, a 2-tuple, or ``None``.  When a component is
    missing or non-finite the Excel embed DPI is used as a sensible default.
    """

    default = EXCEL_EMBED_DPI
    if isinstance(value, (tuple, list)) and value:
        try:
            dpi_x = float(value[0])
        except Exception:
            dpi_x = default
        try:
            dpi_y = float(value[1]) if len(value) > 1 else float(value[0])
        except Exception:
            dpi_y = default
    elif isinstance(value, (int, float)):
        try:
            dpi_x = dpi_y = float(value)
        except Exception:
            dpi_x = dpi_y = default
    else:
        dpi_x = dpi_y = default

    if not math.isfinite(dpi_x) or dpi_x <= 0:
        dpi_x = default
    if not math.isfinite(dpi_y) or dpi_y <= 0:
        dpi_y = default
    return dpi_x, dpi_y


def _image_metrics(image_path: Path) -> Tuple[int, int, float, float]:
    """Return pixel dimensions and DPI metadata for ``image_path``."""

    try:
        from PIL import Image as PILImage
    except Exception:
        return 0, 0, EXCEL_EMBED_DPI, EXCEL_EMBED_DPI

    try:
        with PILImage.open(image_path) as pil_image:
            width_px, height_px = pil_image.size
            dpi_x, dpi_y = _normalise_dpi(pil_image.info.get("dpi"))
    except Exception:
        return 0, 0, EXCEL_EMBED_DPI, EXCEL_EMBED_DPI

    return int(width_px or 0), int(height_px or 0), dpi_x, dpi_y


def _excel_pixel_limits(figure_size: Tuple[float, float]) -> Tuple[int, int]:
    width_in, height_in = figure_size
    width_px = max(int(round(width_in * EXCEL_EMBED_DPI)), 1)
    height_px = max(int(round(height_in * EXCEL_EMBED_DPI)), 1)
    return width_px, height_px
def _excel_row_height(height_in: float) -> float:
    return max(height_in * 72.0, 18.0)


def _column_width_from_pixels(pixels: float) -> float:
    if pixels <= 0:
        return 0.0
    if pixels <= 12.0:
        return pixels / 12.0
    return (pixels - 5.0) / 7.0


def _excel_column_width(width_in: float) -> float:
    pixels = max(width_in * EXCEL_EMBED_DPI, 1.0)
    return max(_column_width_from_pixels(pixels), 8.43)


def _adjust_drawing_ext_dimensions(
    excel_path: Path,
    figure_size: Tuple[float, float],
    log: logging.Logger,
) -> None:
    """Update the drawing CX/CY extents so the width matches the requested figure size."""

    if not excel_path.exists():
        return
    width_in, height_in = figure_size
    width_emu = int(round(width_in * EMU_PER_INCH))
    height_emu = int(round(height_in * EMU_PER_INCH))
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_file.close()
    temp_path = Path(temp_file.name)
    try:
        with ZipFile(excel_path, "r") as source, ZipFile(temp_path, "w") as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename.startswith("xl/drawings/") and info.filename.endswith(".xml"):
                    try:
                        tree = ET.fromstring(data)
                    except ET.ParseError:
                        pass
                    else:
                        updated = False
                        for namespace in (
                            "http://schemas.openxmlformats.org/drawingml/2006/main",
                            "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
                        ):
                            for ext in tree.findall(f".//{{{namespace}}}ext"):
                                ext.set("cx", str(width_emu))
                                ext.set("cy", str(height_emu))
                                updated = True
                        if updated:
                            data = ET.tostring(tree, encoding="utf-8")
                target.writestr(info, data)
        os.replace(temp_path, excel_path)
    except Exception as exc:
        log.warning("Failed to adjust drawing metadata for %s: %s", excel_path, exc)
        try:
            temp_path.unlink()
        except Exception:
            pass


@dataclass
class BuilderConfig:
    """Configuration for the database builder."""

    fabrication_files: List[Path]
    annealing_files: List[Path]
    output_dir: Path
    microscope_files: List[Path] = field(default_factory=list)
    video_files: List[Path] = field(default_factory=list)
    strain_files: List[Path] = field(default_factory=list)
    make_plots: bool = False
    export_formats: Tuple[str, ...] = ("csv",)
    plot_dir_name: str = PLOT_DIR_NAME
    origin_dir_name: str = ORIGIN_DIR_NAME
    output_name: str = DEFAULT_OUTPUT_NAME
    plot_backends: Tuple[str, ...] = ()
    export_behaviour: Optional[Dict[str, str]] = None
    matplotlib_figsize: Tuple[float, float] = DEFAULT_FIGSIZE
    include_microscope_crops: bool = True
    highlight_ocr_values: bool = True


@dataclass
class BuildStats:
    """Accumulates processing statistics."""

    parsed: int = 0
    skipped: int = 0
    missing_draw: int = 0
    missing_piece: int = 0
    resistance_checks_failed: int = 0
    rows_built: int = 0
    missing_high_measurement: int = 0
    missing_low_measurement: int = 0


@dataclass
class OriginArtifact:
    """Metadata describing an Origin object exported for Excel embedding."""

    descriptor: str
    object_path: Optional[Path]
    graph_name: Optional[str] = None
    workbook_name: Optional[str] = None
    worksheet_name: Optional[str] = None
    display_text: Optional[str] = None


@dataclass
class BuildResult:
    """Return value from :func:`build_database`."""

    dataframe: pd.DataFrame
    exports: Dict[str, Path]
    plot_paths: List[str]
    origin_artifacts: Dict[str, OriginArtifact]
    stats: BuildStats
    microscope_crops: Dict[str, Path] = field(default_factory=dict)
    ocr_highlights: Dict[str, Set[int]] = field(default_factory=dict)


@dataclass
class StrainRecord:
    """Single strain measurement parsed from the worksheet."""

    composition: str
    draw: Optional[int]
    piece: Optional[int]
    microwire_label: str
    m_length: Optional[float]
    a_length: Optional[float]
    percent: Optional[float]
    broke: bool
    source: Path


class FabricationIndex:
    """Lookup tables populated from fabrication spreadsheets."""

    def __init__(self) -> None:
        self.draw_level: Dict[Tuple[str, int], Dict[str, object]] = {}
        self.piece_level: Dict[Tuple[str, int, int], Dict[str, object]] = {}

    def set_draw(self, composition: str, draw_x: int, data: Dict[str, object]) -> None:
        key = (composition, draw_x)
        existing = self.draw_level.get(key, {})
        for field, value in data.items():
            if field.endswith("__display") and isinstance(value, (list, tuple)):
                merged: List[object] = []
                for source in (existing.get(field), value):
                    if isinstance(source, (list, tuple)):
                        for item in source:
                            if item not in merged:
                                merged.append(item)
                existing[field] = merged
            else:
                if _has_meaningful_value(value) or field not in existing:
                    existing[field] = value
        self.draw_level[key] = existing

    def set_piece(self, composition: str, draw_x: int, piece_y: int, data: Dict[str, object]) -> None:
        key = (composition, draw_x, piece_y)
        existing = self.piece_level.get(key, {})
        for field, value in data.items():
            if field.endswith("__display") and isinstance(value, (list, tuple)):
                merged: List[object] = []
                for source in (existing.get(field), value):
                    if isinstance(source, (list, tuple)):
                        for item in source:
                            if item not in merged:
                                merged.append(item)
                existing[field] = merged
            else:
                if _has_meaningful_value(value) or field not in existing:
                    existing[field] = value
        self.piece_level[key] = existing

    def get_draw(self, composition: str, draw_x: Optional[int]) -> Dict[str, object]:
        if draw_x is None:
            return {}
        return self.draw_level.get((composition, draw_x), {})

    def get_piece(self, composition: str, draw_x: Optional[int], piece_y: Optional[int]) -> Dict[str, object]:
        if draw_x is None or piece_y is None:
            return {}
        return self.piece_level.get((composition, draw_x, piece_y), {})


@dataclass
class MicroscopeDetection:
    """OCR evidence extracted from a microscope image."""

    value: float
    image_path: Optional[Path]
    bbox: Optional[Tuple[int, int, int, int]] = None  # (left, top, right, bottom)
    text: Optional[str] = None
    source: str = "ocr"
    confidence: Optional[float] = None
    marker: Optional[int] = None
    crop_path: Optional[Path] = None
    category: Optional[str] = None

    def matches(self, value: float, *, tol: float = 0.25) -> bool:
        try:
            delta = abs(float(self.value) - float(value))
        except Exception:
            return False
        return delta <= tol

    def ensure_crop(self, output_dir: Path, prefix: str) -> Optional[Path]:
        """Persist a cropped preview of the detection region."""

        if self.crop_path is not None and self.crop_path.exists():
            return self.crop_path
        if self.image_path is None:
            return None
        try:
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError:
            return None

        crop_bbox = self.bbox
        try:
            with Image.open(self.image_path) as img:
                width = img.width
                height = img.height
                if crop_bbox is not None:
                    left, top, right, bottom = crop_bbox
                    pad = int(round(max(right - left, bottom - top) * 0.25))
                    if pad > 0:
                        left = max(left - pad, 0)
                        top = max(top - pad, 0)
                        right = min(right + pad, width)
                        bottom = min(bottom + pad, height)
                    if right <= left or bottom <= top:
                        crop_bbox = None
                if crop_bbox is None:
                    left, top, right, bottom = 0, 0, width, height
                output_dir.mkdir(parents=True, exist_ok=True)
                safe_prefix = _safe_plot_stem(prefix)
                stem = f"{safe_prefix}_{self.value:.2f}"
                candidate = output_dir / f"{stem}.png"
                counter = 1
                while candidate.exists():
                    candidate = output_dir / f"{stem}_{counter}.png"
                    counter += 1
                region = img.crop((left, top, right, bottom))
                region.save(candidate)
                self.crop_path = candidate
                return candidate
        except Exception:
            return None
        return None


@dataclass
class MicroscopeOCRResult:
    """Container of raw OCR candidates extracted from an image."""

    values: List[float] = field(default_factory=list)
    detections: List[MicroscopeDetection] = field(default_factory=list)
    texts: List[str] = field(default_factory=list)

    def __iter__(self) -> Iterator[float]:  # pragma: no cover - convenience shim
        return iter(self.values)

    def append_value(self, value: float) -> None:
        if not isinstance(value, (int, float)):
            return
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            return
        self.values.append(numeric)


@dataclass
class MicroscopeCacheEntry:
    """Serialized OCR output for a single microscope capture."""

    path: str
    mtime: float
    size: int
    values: List[float] = field(default_factory=list)
    detections: List[Dict[str, Any]] = field(default_factory=list)
    texts: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "mtime": float(self.mtime),
            "size": int(self.size),
            "values": list(self.values),
            "detections": list(self.detections),
            "texts": list(self.texts),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Optional["MicroscopeCacheEntry"]:
        if not isinstance(payload, Mapping):
            return None
        path = str(payload.get("path") or "")
        try:
            mtime = float(payload.get("mtime", 0.0))
        except (TypeError, ValueError):
            mtime = 0.0
        try:
            size = int(payload.get("size", 0))
        except (TypeError, ValueError):
            size = 0
        values_raw = payload.get("values") or []
        detections_raw = payload.get("detections") or []
        texts_raw = payload.get("texts") or []
        values: List[float] = []
        for entry in values_raw:
            try:
                numeric = float(entry)
            except (TypeError, ValueError):
                continue
            values.append(numeric)
        detections: List[Dict[str, Any]] = []
        for entry in detections_raw:
            if isinstance(entry, Mapping):
                detections.append(dict(entry))
        texts: List[str] = []
        for entry in texts_raw:
            text = str(entry)
            if text:
                texts.append(text)
        return cls(path=path, mtime=mtime, size=size, values=values, detections=detections, texts=texts)

    @classmethod
    def from_result(
        cls,
        path: Path,
        mtime: float,
        size: int,
        result: MicroscopeOCRResult,
    ) -> "MicroscopeCacheEntry":
        detections_payload: List[Dict[str, Any]] = []
        for detection in result.detections:
            payload: Dict[str, Any] = {
                "value": float(detection.value),
                "bbox": list(detection.bbox) if detection.bbox else None,
                "text": detection.text,
                "source": detection.source,
                "confidence": detection.confidence,
                "marker": detection.marker,
                "category": detection.category,
                "image_path": str(detection.image_path) if detection.image_path else None,
                "crop_path": str(detection.crop_path) if detection.crop_path else None,
            }
            detections_payload.append(payload)
        return cls(
            path=str(path),
            mtime=float(mtime),
            size=int(size),
            values=list(result.values),
            detections=detections_payload,
            texts=list(result.texts),
        )

    def is_current(self, path: Path) -> bool:
        try:
            stat_result = path.stat()
        except OSError:
            return False
        return (
            math.isclose(float(stat_result.st_mtime), float(self.mtime), rel_tol=0.0, abs_tol=1e-6)
            and int(stat_result.st_size) == int(self.size)
        )

    def to_result(self, path: Path) -> MicroscopeOCRResult:
        detections: List[MicroscopeDetection] = []
        for entry in self.detections:
            value = entry.get("value")
            if not isinstance(value, (int, float)):
                continue
            bbox_source = entry.get("bbox")
            bbox: Optional[Tuple[int, int, int, int]] = None
            if isinstance(bbox_source, (list, tuple)) and len(bbox_source) == 4:
                try:
                    bbox = tuple(int(round(float(coord))) for coord in bbox_source)
                except (TypeError, ValueError):
                    bbox = None
            image_token = entry.get("image_path")
            if image_token:
                try:
                    image_path = Path(str(image_token))
                except Exception:
                    image_path = path
            else:
                image_path = path
            crop_token = entry.get("crop_path")
            crop_path: Optional[Path]
            if crop_token:
                try:
                    crop_candidate = Path(str(crop_token))
                except Exception:
                    crop_candidate = None
                crop_path = crop_candidate
            else:
                crop_path = None
            detection = MicroscopeDetection(
                value=float(value),
                image_path=image_path,
                bbox=bbox,
                text=entry.get("text"),
                source=str(entry.get("source") or "ocr"),
                confidence=float(entry["confidence"]) if isinstance(entry.get("confidence"), (int, float)) else None,
                marker=int(entry["marker"]) if isinstance(entry.get("marker"), (int, float)) else None,
                crop_path=crop_path,
            )
            category = entry.get("category")
            if category is not None:
                detection.category = str(category)
            detections.append(detection)
        return MicroscopeOCRResult(
            values=list(self.values),
            detections=detections,
            texts=list(self.texts),
        )


@dataclass
class MicroscopeMeasurements:
    """Diameter samples gathered from microscope images."""

    core: List[MicroscopeDetection] = field(default_factory=list)
    glass: List[MicroscopeDetection] = field(default_factory=list)
    other: List[MicroscopeDetection] = field(default_factory=list)

    def _target(self, category: str) -> List[MicroscopeDetection]:
        if category == "core":
            return self.core
        if category == "glass":
            return self.glass
        return self.other

    def add_placeholder(self, category: str, image_path: Path) -> None:
        """Ensure at least one detection entry exists for the given image.

        When OCR fails to extract a numeric value we still want the
        microscope worksheet to list the image so the operator can review it
        manually.  A placeholder detection keeps track of the originating
        file without affecting downstream ratio calculations.
        """

        if not image_path:
            return
        target = self._target(category)
        try:
            image_path = Path(image_path)
        except Exception:
            return
        for detection in target:
            existing = getattr(detection, "image_path", None)
            if existing is None:
                continue
            try:
                if Path(existing) == image_path:
                    return
            except Exception:
                continue
        placeholder = MicroscopeDetection(
            value=float("nan"),
            image_path=image_path,
            source="placeholder",
        )
        placeholder.category = category
        target.append(placeholder)

    def extend(
        self,
        category: str,
        values: Iterable[float],
        detections: Iterable[MicroscopeDetection] | None = None,
    ) -> None:
        target = self._target(category)
        evidence = list(detections or [])
        for detection in evidence:
            detection.category = category
        source = "manual"
        if detections is not None:
            source = "ocr"
        for value in values:
            if not isinstance(value, (int, float)):
                continue
            numeric = float(value)
            if not math.isfinite(numeric) or numeric <= 0:
                continue
            match = None
            for candidate in list(evidence):
                if candidate.matches(numeric):
                    match = candidate
                    evidence.remove(candidate)
                    break
            if match is None:
                match = MicroscopeDetection(
                    value=numeric,
                    image_path=None,
                    source=source,
                )
                match.category = category
            target.append(match)

    def _best_detection_sequence(
        self, categories: Sequence[str], prefer: str
    ) -> Optional[MicroscopeDetection]:
        for category in categories:
            target = self._target(category)
            if not target:
                continue
            value = _select_microscope_value([entry.value for entry in target], prefer)
            if value is None:
                continue
            chosen = self._match_detection(target, value)
            if chosen is not None:
                return chosen
        return None

    @staticmethod
    def _match_detection(
        candidates: Sequence[MicroscopeDetection], value: float
    ) -> Optional[MicroscopeDetection]:
        best: MicroscopeDetection | None = None
        best_delta: float | None = None
        for detection in candidates:
            try:
                delta = abs(float(detection.value) - float(value))
            except Exception:
                continue
            if best is None or (best_delta is not None and delta < best_delta) or best_delta is None:
                best = detection
                best_delta = delta
        return best

    def best_core(self) -> Optional[float]:
        detection = self.best_core_detection()
        return detection.value if detection is not None else None

    def best_core_detection(self) -> Optional[MicroscopeDetection]:
        return self._best_detection_sequence(("core", "other", "glass"), "min")

    def best_glass_detection(self) -> Optional[MicroscopeDetection]:
        return self._best_detection_sequence(("glass", "other", "core"), "max")

    def best_glass(self) -> Optional[float]:
        detection = self.best_glass_detection()
        return detection.value if detection is not None else None


@dataclass
class VideoMetricsSummary:
    """Aggregated metrics extracted from fabrication videos."""

    temperatures: List[float] = field(default_factory=list)
    underpressures: List[float] = field(default_factory=list)
    winding_speeds: List[float] = field(default_factory=list)
    glass_feeds: List[float] = field(default_factory=list)
    sources: set[Path] = field(default_factory=set)

    def record(self, result, *, source: Optional[Path] = None) -> None:
        temp = getattr(result, "median_temperature", None)
        if callable(temp):
            value = temp()
        else:
            value = None
        if value is not None and isinstance(value, (int, float)) and math.isfinite(float(value)):
            self.temperatures.append(float(value))
        under_fn = getattr(result, "median_underpressure", None)
        if callable(under_fn):
            under_value = under_fn()
        else:
            under_value = None
        if under_value is not None and isinstance(under_value, (int, float)) and math.isfinite(float(under_value)):
            self.underpressures.append(float(under_value))
        speed_fn = getattr(result, "median_winding_speed", None)
        if callable(speed_fn):
            speed_value = speed_fn()
        else:
            speed_value = None
        if speed_value is not None and isinstance(speed_value, (int, float)) and math.isfinite(float(speed_value)):
            self.winding_speeds.append(float(speed_value))
        feed_fn = getattr(result, "median_glass_feed", None)
        if callable(feed_fn):
            feed_value = feed_fn()
        else:
            feed_value = None
        if feed_value is not None and isinstance(feed_value, (int, float)) and math.isfinite(float(feed_value)):
            self.glass_feeds.append(float(feed_value))
        if source is not None:
            try:
                self.sources.add(Path(source))
            except Exception:
                pass

    def temperature(self) -> Optional[float]:
        return _select_microscope_value(self.temperatures, "median", allow_negative=True)

    def underpressure(self) -> Optional[float]:
        return _select_microscope_value(self.underpressures, "median", allow_negative=True)

    def winding_speed(self) -> Optional[float]:
        return _select_microscope_value(self.winding_speeds, "median")

    def glass_feed(self) -> Optional[float]:
        return _select_microscope_value(self.glass_feeds, "median")


def _logger(logger: Optional[logging.Logger]) -> logging.Logger:
    if logger is not None:
        return logger
    return logging.getLogger(LOGGER_NAME)


def _normalise_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("Âµ", "µ").replace("�", "µ").replace("ï¿½", "µ")
    return text


def _is_unit_suffix(text: str) -> bool:
    """Return True when *text* only contains unit markers such as µm."""

    if not text:
        return False
    stripped = unicodedata.normalize("NFKC", text).strip()
    if not stripped:
        return False
    ascii_text = unicodedata.normalize("NFKD", stripped)
    ascii_text = "".join(ch for ch in ascii_text if not unicodedata.combining(ch))
    simplified = re.sub(r"\s+", "", ascii_text).lower()
    canonical_units = {"um", "µm", "μm"}
    if simplified in canonical_units | {"(um)", "(µm)", "(μm)", "um)", "µm)", "μm)"}:
        return True
    unit_chars = set("uµμm()/.-[]{}")
    if simplified and all(ch in unit_chars for ch in simplified):
        return True
    return False

def _merged_header_row(
    df: pd.DataFrame, header_idx: int, *, lookback: int = 3
) -> List[object]:
    """Return a header row with empty cells backfilled from previous rows."""

    if header_idx < 0 or header_idx >= len(df):
        return []

    header = list(df.iloc[header_idx])
    if header_idx == 0:
        return header

    max_offset = min(max(header_idx, 0), max(0, lookback))
    if max_offset <= 0:
        return header

    for col_idx in range(len(header)):
        value = header[col_idx]
        text = _normalise_text(value) if value is not None and not _is_nan(value) else ""
        if text and _is_unit_suffix(text):
            prefix: Optional[str] = None
            for offset in range(1, max_offset + 1):
                prev_idx = header_idx - offset
                if prev_idx < 0:
                    break
                try:
                    candidate = df.iat[prev_idx, col_idx]
                except (IndexError, ValueError):
                    candidate = None
                if candidate is None or _is_nan(candidate):
                    continue
                candidate_text = _normalise_text(candidate)
                if not candidate_text or _is_unit_suffix(candidate_text):
                    continue
                prefix = candidate_text
                break
            if prefix:
                combined = f"{prefix} {text}".strip()
                header[col_idx] = combined
                continue
        if text:
            continue
        for offset in range(1, max_offset + 1):
            try:
                candidate = df.iat[header_idx - offset, col_idx]
            except (IndexError, ValueError):
                candidate = None
            if candidate is None or _is_nan(candidate):
                continue
            candidate_text = _normalise_text(candidate)
            if candidate_text:
                header[col_idx] = candidate_text
                break
    return header


def _header_key(value: object) -> Optional[str]:
    text = _normalise_text(value)
    if not text:
        return None
    hint = HEADER_HINTS.get(text)
    if hint:
        return hint
    if text in {"d (�m)", "D (�m)"}:
        text = text.replace("�", "µ")
        hint = HEADER_HINTS.get(text)
        if hint:
            return hint
    ascii_text = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in ascii_text if not unicodedata.combining(ch))
    ascii_hint = HEADER_HINTS.get(ascii_text)
    if ascii_hint:
        return ascii_hint
    ascii_hint = HEADER_HINTS.get(ascii_text.lower())
    if ascii_hint:
        return ascii_hint
    lowered = ascii_text.lower()
    simple = lowered.replace("\u00a0", " ")
    simple = re.sub(r"\s+", " ", simple)
    simple_compact = re.sub(r"[^a-z0-9]+", "", lowered)
    if "zlozen" in lowered:
        return "composition_label"
    if "datum" in lowered and "cas" in lowered:
        return "production_datetime"
    if "hmotn" in lowered or "mass" in lowered:
        return "mass_g"
    if "odpor" in lowered or "resistance" in lowered:
        return "fabrication_resistance_ohm"
    if "teplot" in lowered or "temp" in lowered:
        return "fabrication_temperature_c"
    if "winding" in lowered:
        return "winding_speed_m_per_min"
    if "glass" in lowered and ("feed" in lowered or "feeding" in lowered):
        return "glass_feed_mm_per_min"
    if "underpressure" in lowered:
        return "underpressure"
    if "bistabil" in lowered:
        return "bistable_status"
    if "dtum" in lowered and "cas" not in lowered:
        return "piece_date"
    if "p.c" in lowered or "p.Ä" in lowered or simple_compact == "pc":
        return "piece_y"
    if "pocet" in lowered and "otac" in lowered:
        return "piece_turns"
    if "poet" in lowered and "otok" in lowered:
        return "piece_turns"
    if "dlzk" in lowered or "dlÅ¾k" in lowered:
        return "length_m"
    micron_hint = any(token in lowered for token in ("Âµm", "µm", "um", "Î¼m", "mikro", "micro"))
    ascii_simple = ascii_text.replace("\u00a0", " ")
    if micron_hint:
        if re.search(r"\bD\d*\b", ascii_text):
            return "D_um"
        if re.search(r"\bd\d*\b", ascii_text):
            return "d_um"
        if "glass" in lowered or "sklo" in lowered:
            return "D_um"
        if any(token in lowered for token in ("jadro", "jadra", "jadier", "core")):
            return "d_um"
        if re.search(r"\bd\s*/\s*D\b", ascii_simple, re.IGNORECASE):
            return "d_over_D"
    if lowered.strip().startswith("d") and any(token in lowered for token in ("Âµm", "µm")):
        first = ascii_text.strip()[:1]
        if first == "D":
            return "D_um"
        return "d_um"
    if lowered.strip().startswith("d") and "um" in lowered and all(token not in lowered for token in ("Âµ", "µ")):
        first = ascii_text.strip()[:1]
        if first == "D":
            return "D_um"
        return "d_um"
    if micron_hint and "d" in lowered and "d" in ascii_simple and "D" in ascii_simple:
        return "d_over_D"
    if "d/d" in lowered or simple_compact == "dd":
        return "d_over_D"
    if any(token in lowered for token in ("jadro", "jadra", "jadier", "core")) and re.search(r"\bd\d*\b", ascii_text):
        return "d_um"
    if any(token in lowered for token in ("sklo", "skla", "glass", "clad", "cladding", "sheath")) and re.search(r"\bD\d*\b", ascii_text):
        return "D_um"
    if ("datum" in lowered or "dÃ¡tum" in lowered) and "cas" not in lowered:
        return "piece_date"
    return None


def _is_nan(value: object) -> bool:
    if isinstance(value, (float, np.floating)):
        try:
            return math.isnan(float(value))
        except ValueError:
            return True
    if value is None:
        return False
    try:
        if value is pd.NA:  # type: ignore[attr-defined]
            return True
    except AttributeError:
        pass
    try:
        result = pd.isna(value)
    except Exception:
        return False
    if isinstance(result, (bool, np.bool_)):
        return bool(result)
    return False


def _raw_string(value: object) -> Optional[str]:
    if value is None or _is_nan(value):
        return None
    text = _normalise_text(value)
    return text if text else None


def _parse_numeric(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not _is_nan(value):
        return float(value)
    text = _normalise_text(value)
    if not text:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    number = match.group(0).replace(",", ".")
    try:
        return float(number)
    except ValueError:
        return None


def _clean_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not _is_nan(value):
        text = f"{value}"
    else:
        text = _normalise_text(value)
    return text.strip() if text else ""


def _has_meaningful_value(value: object) -> bool:
    if value is None:
        return False
    if _is_nan(value):
        return False
    if isinstance(value, str):
        return bool(_clean_str(value))
    return True


def _parse_strain_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not _is_nan(value):
        return float(value)
    text = _clean_str(value)
    if not text:
        return None
    upper = text.upper()
    if "DIV/0" in upper or "INF" in upper or "BROKE" in upper:
        return None
    return _parse_numeric(text)


def _microwire_tuple_from_label(label: str) -> Optional[Tuple[int, int]]:
    match = MICROWIRE_LABEL_RE.search(label)
    if not match:
        return None
    try:
        return int(match.group(1)), int(match.group(2))
    except ValueError:
        return None


def _format_strain_value(record: StrainRecord) -> Optional[str]:
    if record.broke:
        return "broke"
    if record.percent is None:
        return None
    return f"{record.percent:.3f}%"


def _sanitize_datetime_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"(?<=\d)/(?=\d{1,2}(?::\d{1,2})?)", " ", cleaned)
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned)
    tokens: list[str] = []
    for raw_token in cleaned.split():
        token = raw_token.strip().strip(",.;")
        if not token:
            continue
        tokens.append(token)
    while tokens:
        candidate = tokens[-1]
        upper = candidate.upper()
        if any(char.isdigit() for char in candidate):
            break
        if upper in KNOWN_TIMEZONE_TOKENS:
            break
        tokens.pop()
    return " ".join(tokens)


def _parse_datetime(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    text = _normalise_text(value)
    if not text:
        return None
    text = _sanitize_datetime_text(text)
    try:
        dayfirst: Optional[bool]
        if DOT_DATE_PATTERN.search(text):
            dayfirst = True
        elif SLASH_DATE_PATTERN.search(text):
            dayfirst = False
        else:
            dayfirst = None
        if dayfirst is None:
            dt = pd.to_datetime(text, dayfirst=False, errors="coerce")
            if pd.isna(dt):
                dt = pd.to_datetime(text, dayfirst=True, errors="coerce")
        else:
            dt = pd.to_datetime(text, dayfirst=dayfirst, errors="coerce")
    except (TypeError, ValueError):
        return None
    if pd.isna(dt):
        return None
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    if isinstance(dt, datetime):
        return dt.isoformat(sep=" ")
    return None


def _parse_date(value: object) -> Optional[str]:
    parsed = _parse_datetime(value)
    if not parsed:
        return None
    try:
        dt = datetime.fromisoformat(parsed)
    except ValueError:
        return parsed
    return dt.date().isoformat()


def _select_microscope_value(
    values: Sequence[float], prefer: str, *, allow_negative: bool = False
) -> Optional[float]:
    cleaned: List[float] = []
    for v in values:
        if not isinstance(v, (int, float)):
            continue
        value = float(v)
        if not math.isfinite(value):
            continue
        if not allow_negative and value <= 0:
            continue
        cleaned.append(value)
    if not cleaned:
        return None
    if prefer == "min":
        return float(min(cleaned))
    if prefer == "max":
        return float(max(cleaned))
    cleaned.sort()
    mid = len(cleaned) // 2
    if len(cleaned) % 2:
        return float(cleaned[mid])
    return float((cleaned[mid - 1] + cleaned[mid]) / 2.0)


def _extract_field_value(field: str, value: object) -> Tuple[Optional[object], Optional[str]]:
    raw = _raw_string(value)
    if field in DATETIME_FIELDS:
        return _parse_datetime(value), raw
    if field in DATE_FIELDS:
        return _parse_date(value), raw
    if field in DRAW_NUMERIC_FIELDS or field in PIECE_NUMERIC_FIELDS:
        return _parse_numeric(value), raw
    if field == "fabrication_temperature_c":
        numeric = _parse_numeric(value)
        return numeric if numeric is not None else raw, raw
    if value is None or _is_nan(value):
        return None, raw
    return _normalise_text(value), raw


def _format_dimension_display(field: str, value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not _is_nan(value):
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        precision = 3
        formatted = f"{numeric:.{precision}f}".rstrip("0").rstrip(".")
        return formatted or f"{numeric:.{precision}f}"
    if isinstance(value, str):
        numeric = _parse_numeric(value)
        if numeric is not None:
            precision = 3
            formatted = f"{numeric:.{precision}f}".rstrip("0").rstrip(".")
            return formatted or f"{numeric:.{precision}f}"
    text = _clean_str(value)
    return text or None


def _canonical_dimension_field(field: Optional[str]) -> Optional[str]:
    """Map parsed spreadsheet headers to canonical diameter buckets."""

    if not field:
        return None
    if field in DIMENSION_FIELDS:
        return field
    base = field[:-4] if field.endswith("_raw") else field
    simplified = base.replace("__", "_")
    lowered = simplified.lower()
    cleaned = simplified.strip()
    lowered_clean = cleaned.lower()

    if simplified in DIMENSION_FIELDS:
        return simplified
    lowered_map = {name.lower(): name for name in DIMENSION_FIELDS}
    matched = lowered_map.get(lowered)
    if matched is not None:
        return matched

    if "d_over_d" in lowered or "doverd" in lowered or "ratio_d" in lowered:
        return "d_over_D"

    if lowered_clean in {"d", "d.", "d:"}:
        return "d_um"
    if cleaned in {"D", "D.", "D:"}:
        return "D_um"

    has_micron_hint = any(token in lowered for token in ("_um", " um", "Âµm", "Î¼m", "mic"))
    context_hint = any(
        token in lowered
        for token in (
            "core",
            "jadro",
            "jadra",
            "jadier",
            "glass",
            "sklo",
            "clad",
            "cladding",
            "sheath",
            "outer",
            "inner",
        )
    )
    if context_hint and any(term in lowered for term in ("feed", "feeding", "speed")):
        context_hint = False

    if not (has_micron_hint or context_hint):
        return None

    if any(token in lowered for token in ("core", "jadro", "jadra", "jadier", "inner")):
        return "d_um"
    if any(token in lowered for token in ("glass", "sklo", "clad", "cladding", "sheath", "outer")):
        return "D_um"
    first = simplified[:1]
    if first == "d":
        return "d_um"
    if first == "D":
        return "D_um"
    return None


def _append_dimension_display(
    record: Dict[str, object],
    field: str,
    parsed: Optional[object],
    raw: Optional[str],
) -> None:
    canonical = _canonical_dimension_field(field)
    if canonical not in DIMENSION_FIELDS:
        return
    key = f"{canonical}__display"
    bucket = record.get(key)
    if not isinstance(bucket, list):
        bucket = [] if bucket is None else list(bucket if isinstance(bucket, (list, tuple)) else [bucket])
    for candidate in (parsed, raw):
        text = _format_dimension_display(canonical, candidate)
        if not text:
            continue
        if text not in bucket:
            bucket.append(text)
    record[key] = bucket


def _composition_from_path(path: Path) -> str:
    stem = path.stem
    return stem.split()[0]


def _extract_composition_token(text: str) -> Optional[str]:
    if not text:
        return None
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]+", text)
    if not tokens:
        return None
    return tokens[0]


def _microscope_key(path: Path) -> Optional[Tuple[str, int, int]]:
    def _match(text: str) -> Optional[Tuple[str, int, int]]:
        if not text:
            return None
        composition = _extract_composition_token(text)
        if not composition or not any(ch.isdigit() for ch in composition):
            return None

        def _to_pair(match: re.Match[str]) -> Optional[Tuple[str, int, int]]:
            try:
                draw_x = int(match.group(1))
                piece_y = int(match.group(2))
            except (TypeError, ValueError):
                return None
            return composition, draw_x, piece_y

        for pattern in MICROSCOPE_PAIR_PATTERNS:
            normalised = text
            if pattern is MICROSCOPE_PAIR_PATTERNS[1]:
                normalised = normalised.replace("-", "/")
            match = pattern.search(normalised)
            if match:
                if pattern is MICROSCOPE_PAIR_PATTERNS[1]:
                    following = normalised[match.end(): match.end() + 1]
                    if following and following in "-/":
                        continue
                pair = _to_pair(match)
                if pair is not None:
                    return pair

        for match in MICROSCOPE_WHITESPACE_PAIR.finditer(text):
            pair = _to_pair(match)
            if pair is not None:
                return pair

        tokens = re.split(r"\s+", text)
        for token in tokens:
            if not token:
                continue
            normalised = token.replace("-", "_").replace("/", "_")
            match = XY_PATTERN.search(normalised)
            if not match:
                continue
            pair = _to_pair(match)
            if pair is not None:
                return pair
        return None

    for candidate in (path.stem, path.name):
        result = _match(candidate)
        if result is not None:
            return result
    for parent in path.parents:
        result = _match(parent.name)
        if result is not None:
            return result
    return None


def _microscope_category(path: Path) -> str:
    stem = path.stem.lower()
    if "core" in stem:
        return "core"
    if "glass" in stem:
        return "glass"
    return "other"


def _iter_fragment_files(
    root: Path,
    fragment: str,
    extensions: Sequence[str],
    *,
    max_depth: int = 3,
    limit: Optional[int] = None,
) -> List[Path]:
    if root is None:
        return []
    try:
        if not root.exists():
            return []
    except OSError:
        return []

    fragment_lower = fragment.lower()
    matches: List[Path] = []
    stack: List[Tuple[Path, int]] = [(root, 0)]
    visited: Set[Path] = set()
    while stack:
        current, depth = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if depth >= max_depth:
                        continue
                    try:
                        key = entry.resolve()
                    except OSError:
                        key = entry
                    if key in visited:
                        continue
                    visited.add(key)
                    name_lower = entry.name.lower()
                    if depth == 0 or fragment_lower in name_lower:
                        stack.append((entry, depth + 1))
                elif entry.is_file():
                    if entry.suffix.lower() in extensions and fragment_lower in entry.name.lower():
                        matches.append(entry)
                        if limit is not None and len(matches) >= limit:
                            return matches
            except OSError:
                continue
    return matches


def _auto_discover_microscope_paths(
    annealing_files: Sequence[Path], logger: logging.Logger
) -> List[Path]:
    discovered: List[Path] = []
    seen: Set[Path] = set()
    for raw_path in annealing_files:
        path = Path(raw_path)
        key = _microscope_key(path)
        if key is None:
            continue
        composition, draw_x, piece_y = key
        fragment = f"{draw_x}_{piece_y}"
        try:
            resolved = path.resolve()
            parents = list(resolved.parents)
        except OSError:
            parents = list(path.parents)
        candidate_roots: List[Path] = []
        for parent in parents:
            for name in ("microscope", "Microscope"):
                candidate = parent / name
                try:
                    if candidate.is_dir():
                        candidate_roots.append(candidate)
                except OSError:
                    continue
            if candidate_roots:
                break
        if not candidate_roots:
            continue
        lowered = composition.lower()
        search_dirs: List[Path] = []
        for root_dir in candidate_roots:
            search_dirs.append(root_dir)
            try:
                direct = root_dir / composition
                if direct.is_dir():
                    search_dirs.append(direct)
                for child in root_dir.iterdir():
                    if child.is_dir() and child.name.lower().startswith(lowered):
                        search_dirs.append(child)
            except OSError:
                continue
        for search_dir in dict.fromkeys(search_dirs):
            matches = _iter_fragment_files(search_dir, fragment, MICROSCOPE_EXTENSIONS, limit=20)
            for match in matches:
                try:
                    resolved_match = match.resolve()
                except OSError:
                    resolved_match = match
                if resolved_match in seen:
                    continue
                seen.add(resolved_match)
                discovered.append(match)
    if discovered:
        logger.debug("Auto-discovered %s microscope image(s)", len(discovered))
    return discovered


def _normalise_microscope_text(text: str) -> str:
    cleaned = unicodedata.normalize("NFKC", text or "")
    cleaned = cleaned.replace("Î¼", MICRO_SIGN)
    cleaned = cleaned.replace("|", "1")
    cleaned = re.sub(r"(^|\s)(?:1|I|l){1,2}\]", lambda m: f"{m.group(1)}[1]", cleaned)
    cleaned = re.sub(r"(^|\s)(?:2|Z)\]", lambda m: f"{m.group(1)}[2]", cleaned)

    def _marker_replacer(match: re.Match[str]) -> str:
        digit = match.group("digit")
        if digit in {"I", "l"}:
            digit = "1"
        if digit not in {"1", "2"}:
            digit = "1"
        return f"[{digit}]"

    cleaned = MICROSCOPE_MARKER_PATTERN.sub(_marker_replacer, cleaned)
    cleaned = re.sub(r"\[1(?=\s*\d)", "[1]", cleaned)
    cleaned = re.sub(r"\[2(?=\s*\d)", "[2]", cleaned)
    return cleaned


def _is_secondary_prefix(prefix: str) -> bool:
    return bool(MICROSCOPE_SECONDARY_PREFIX.search(prefix.strip()))


def _has_primary_marker(prefix: str) -> bool:
    snippet = unicodedata.normalize("NFKC", prefix[-8:] if prefix else "")
    if not snippet:
        return False
    snippet = snippet.replace("Î¼", MICRO_SIGN)
    snippet = snippet.replace("|", "1").replace("I", "1").replace("l", "1")
    snippet = snippet.replace("{", "[").replace("(", "[")
    snippet = snippet.replace("}", "]").replace(")", "]")
    snippet = snippet.strip()
    if not snippet:
        return False
    if "[1" in snippet:
        return True
    if "1]" in snippet:
        return True
    return bool(MICROSCOPE_PRIMARY_HINT.search(snippet))


def _parse_microscope_candidates(texts: Iterable[str]) -> List[float]:
    preferred: Dict[float, float] = {}
    fallback_with_marker: Dict[float, float] = {}
    fallback_loose: Dict[float, float] = {}
    for raw_text in texts:
        if not raw_text:
            continue
        text = _normalise_microscope_text(raw_text)
        found_primary = False
        for match in MICROSCOPE_PRIMARY_PATTERN.finditer(text):
            raw_value = match.group("value").replace(",", ".")
            try:
                value = float(raw_value)
            except ValueError:
                continue
            if not math.isfinite(value) or value <= 0:
                continue
            key = round(value, 2)
            preferred.setdefault(key, value)
            found_primary = True
        if found_primary:
            continue
        if preferred:
            continue
        for match in MICROSCOPE_VALUE_PATTERN.finditer(text):
            start = max(match.start() - 6, 0)
            prefix = text[start:match.start()]
            if _is_secondary_prefix(prefix):
                continue
            raw_value = match.group("value").replace(",", ".")
            try:
                value = float(raw_value)
            except ValueError:
                continue
            if not math.isfinite(value) or value <= 0:
                continue
            if value > 1000:
                continue
            key = round(value, 2)
            if _has_primary_marker(prefix):
                fallback_with_marker.setdefault(key, value)
            else:
                fallback_loose.setdefault(key, value)
    if preferred:
        selected = preferred
    elif fallback_with_marker:
        selected = fallback_with_marker
    else:
        selected = fallback_loose
    return [float(v) for v in selected.values()]


def _extract_microscope_diameters(
    path: Path,
    logger: logging.Logger,
) -> MicroscopeOCRResult:
    """Attempt to OCR diameter annotations from a microscope capture."""

    log = logger or logging.getLogger(LOGGER_NAME)
    ocr = get_paddle_ocr(log)
    if ocr is None:
        log.warning(
            "PaddleOCR is not available; skipping microscope OCR for %s",
            path,
        )
        return MicroscopeOCRResult()

    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps  # type: ignore[import-not-found]
    except ImportError:
        log.warning("Pillow is not installed; skipping microscope OCR for %s", path)
        return MicroscopeOCRResult()

    try:
        with Image.open(path) as img:
            base = img.convert("RGB")
    except Exception:
        log.warning("Failed to open microscope image %s", path, exc_info=True)
        return MicroscopeOCRResult()

    result = MicroscopeOCRResult()
    original_width, original_height = base.size

    def _resample(image):
        width, height = image.size
        target = MICROSCOPE_RESIZE_TARGET
        longest = max(width, height)
        if longest <= 0 or longest <= target:
            return image
        scale = target / float(longest)
        if scale >= 1.0 or math.isclose(scale, 1.0, rel_tol=1e-3):
            return image
        new_size = (max(int(round(width * scale)), 1), max(int(round(height * scale)), 1))
        resample_attr = getattr(Image, "Resampling", None)
        if resample_attr is not None:
            resample_filter = getattr(resample_attr, "LANCZOS", Image.BICUBIC)
        else:
            resample_filter = getattr(Image, "LANCZOS", Image.BICUBIC)
        return image.resize(new_size, resample_filter)

    base_resized = _resample(base)
    resized_width, resized_height = base_resized.size
    if resized_width <= 0 or resized_height <= 0:
        resized_width, resized_height = original_width, original_height
    scale_to_original_x = (
        original_width / float(resized_width) if resized_width else 1.0
    )
    scale_to_original_y = (
        original_height / float(resized_height) if resized_height else 1.0
    )

    candidates: List[str] = []
    seen_candidates: Set[str] = set()

    def _append_candidate(text: Optional[str]) -> None:
        if not text:
            return
        cleaned = text.strip()
        if not cleaned or cleaned in seen_candidates:
            return
        candidates.append(cleaned)
        seen_candidates.add(cleaned)

    grayscale = ImageOps.grayscale(base_resized)

    variant_cache: Dict[str, Optional[Image.Image]] = {
        "base": base_resized,
        "grayscale": grayscale,
    }

    def _variant(name: str, factory: Callable[[], Optional[Image.Image]]) -> Optional[Image.Image]:
        if name not in variant_cache:
            try:
                variant_cache[name] = factory()
            except Exception:
                variant_cache[name] = None
        return variant_cache.get(name)

    def _make_contrast() -> Optional[Image.Image]:
        gray = variant_cache.get("grayscale")
        if gray is None:
            return None
        return ImageEnhance.Contrast(gray).enhance(2.0)

    def _make_sharpen() -> Optional[Image.Image]:
        contrast_img = _variant("contrast", _make_contrast)
        if contrast_img is None:
            return None
        return contrast_img.filter(ImageFilter.UnsharpMask(radius=2, percent=175))

    def _make_autocontrast() -> Optional[Image.Image]:
        gray = variant_cache.get("grayscale")
        if gray is None:
            return None
        return ImageOps.autocontrast(gray)

    def _make_invert() -> Optional[Image.Image]:
        gray = variant_cache.get("grayscale")
        if gray is None:
            return None
        return ImageOps.invert(gray)

    def _make_binary() -> Optional[Image.Image]:
        gray = variant_cache.get("grayscale")
        if gray is None:
            return None
        binary = gray.point(lambda p: 255 if p > 160 else 0, mode="1")
        return ImageOps.autocontrast(binary.convert("L"))

    def _make_binary_invert() -> Optional[Image.Image]:
        binary_img = _variant("binary", _make_binary)
        if binary_img is None:
            return None
        return ImageOps.invert(binary_img)

    def _fourier_sharpen(image: Image.Image) -> Image.Image:
        array = np.array(image.convert("L"), dtype=np.float32)
        if array.ndim != 2 or array.size == 0:
            return image
        rows, cols = array.shape
        crow, ccol = rows // 2, cols // 2
        if crow == 0 or ccol == 0:
            return image
        freq = np.fft.fft2(array)
        shift = np.fft.fftshift(freq)
        y = np.arange(rows, dtype=np.float32)[:, None]
        x = np.arange(cols, dtype=np.float32)[None, :]
        distance = np.sqrt((y - float(crow)) ** 2 + (x - float(ccol)) ** 2)
        radius = max(min(rows, cols) * 0.08, 1.0)
        mask = np.ones_like(shift, dtype=np.complex128)
        mask[distance <= radius] = 0.1
        filtered = shift * mask
        inv_shift = np.fft.ifftshift(filtered)
        sharpened = np.fft.ifft2(inv_shift).real
        sharpened = np.clip(sharpened, 0, 255)
        try:
            return Image.fromarray(sharpened.astype("uint8"))
        except Exception:
            return image

    def _red_enhance(image: Image.Image) -> Optional[Image.Image]:
        try:
            array = np.array(image.convert("RGB"), dtype=np.float32)
        except Exception:
            return None
        if array.ndim != 3 or array.shape[2] != 3:
            return None
        red = array[..., 0]
        green = array[..., 1]
        blue = array[..., 2]
        emphasised = red - 0.45 * green - 0.45 * blue + 80.0
        emphasised = np.clip(emphasised, 0, 255)
        emphasised = emphasised.astype("uint8")
        enhanced_image = Image.fromarray(emphasised, mode="L")
        return ImageOps.autocontrast(enhanced_image)

    def _make_fourier() -> Optional[Image.Image]:
        gray = variant_cache.get("grayscale")
        if gray is None:
            return None
        return _fourier_sharpen(gray)

    def _make_red_mask() -> Optional[Image.Image]:
        return _red_enhance(base_resized)

    def _make_red_binary() -> Optional[Image.Image]:
        mask = _variant("red_mask", _make_red_mask)
        if mask is None:
            return None
        return mask.point(lambda p: 255 if p > 140 else 0, mode="1").convert("L")

    def _iter_variants() -> Iterable[Tuple[str, Optional[Image.Image]]]:
        yield "base", variant_cache["base"]
        yield "grayscale", variant_cache["grayscale"]
        yield "contrast", _variant("contrast", _make_contrast)
        yield "sharpen", _variant("sharpen", _make_sharpen)
        yield "autocontrast", _variant("autocontrast", _make_autocontrast)
        yield "invert", _variant("invert", _make_invert)
        yield "binary", _variant("binary", _make_binary)
        yield "binary_invert", _variant("binary_invert", _make_binary_invert)
        yield "red_mask", _variant("red_mask", _make_red_mask)
        yield "red_binary", _variant("red_binary", _make_red_binary)
        yield "fourier", _variant("fourier", _make_fourier)

    focus_variants: List[Tuple[str, Image.Image, Tuple[int, int]]] = []
    try:  # pragma: no cover - optional dependency
        import cv2  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - optional dependency
        cv2 = None  # type: ignore[assignment]

    def _append_focus_crop(label: str, rect: Tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = rect
        width = max(x1 - x0, 0)
        height = max(y1 - y0, 0)
        if width < 40 or height < 25:
            return
        try:
            crop = base_resized.crop((x0, y0, x1, y1))
        except Exception:
            return
        focus_variants.append((label, crop, (x0, y0)))

    if cv2 is not None:
        try:
            base_array = np.array(base_resized.convert("RGB"))
        except Exception:
            base_array = None
        if base_array is not None and base_array.ndim == 3 and base_array.shape[2] == 3:
            try:
                bgr = base_array[:, :, ::-1]
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            except Exception:
                gray = None
                bgr = None
            if gray is not None:
                blur = cv2.GaussianBlur(gray, (5, 5), 0)
                _, mask = cv2.threshold(blur, 200, 255, cv2.THRESH_BINARY)
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
                mask = cv2.dilate(mask, kernel, iterations=1)

                def _collect_rois(mask_array: np.ndarray, prefix: str) -> None:
                    try:
                        contours_info = cv2.findContours(
                            mask_array, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                        )
                    except Exception:
                        return
                    if len(contours_info) == 3:
                        _, contours, _ = contours_info
                    else:
                        contours, _ = contours_info
                    rois: List[Tuple[int, int, int, int]] = []
                    for contour in contours or []:
                        try:
                            x, y, w, h = cv2.boundingRect(contour)
                        except Exception:
                            continue
                        if w <= 0 or h <= 0:
                            continue
                        aspect = w / float(h)
                        area = w * h
                        if w < 50 or h < 24 or aspect < 0.9 or aspect > 11.0 or area < 1500:
                            continue
                        pad_x = int(round(max(w, 60) * 0.35))
                        pad_y = int(round(max(h, 40) * 0.45))
                        x0 = max(x - pad_x, 0)
                        y0 = max(y - pad_y, 0)
                        x1 = min(x + w + pad_x, resized_width)
                        y1 = min(y + h + pad_y, resized_height)
                        if x1 - x0 < 40 or y1 - y0 < 25:
                            continue
                        rois.append((x0, y0, x1, y1))
                    rois.sort(key=lambda rect: (rect[0], rect[1]))
                    for idx, rect in enumerate(rois[:FOCUS_ROI_LIMIT]):
                        _append_focus_crop(f"focus{prefix}{idx + 1}", rect)

                _collect_rois(mask, "")

                if bgr is not None:
                    try:
                        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
                    except Exception:
                        hsv = None
                    if hsv is not None:
                        lower_red1 = np.array([0, 90, 110], dtype=np.uint8)
                        upper_red1 = np.array([12, 255, 255], dtype=np.uint8)
                        lower_red2 = np.array([160, 90, 110], dtype=np.uint8)
                        upper_red2 = np.array([180, 255, 255], dtype=np.uint8)
                        mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
                        mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
                        red_mask_cv = cv2.bitwise_or(mask1, mask2)
                        if red_mask_cv is not None:
                            red_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
                            red_mask_cv = cv2.morphologyEx(
                                red_mask_cv, cv2.MORPH_CLOSE, red_kernel, iterations=2
                            )
                            red_mask_cv = cv2.dilate(red_mask_cv, red_kernel, iterations=1)
                            _collect_rois(red_mask_cv, "r")

    if not focus_variants:
        try:
            base_array = np.array(base_resized.convert("RGB"))
        except Exception:
            base_array = None
        if base_array is not None and base_array.ndim == 3:
            red = base_array[:, :, 0].astype(np.float32)
            green = base_array[:, :, 1].astype(np.float32)
            blue = base_array[:, :, 2].astype(np.float32)
            emphasised = red - 0.6 * green - 0.4 * blue
            mask = emphasised > 50.0
            coords = np.argwhere(mask)
            if coords.size:
                y0 = int(coords[:, 0].min())
                y1 = int(coords[:, 0].max()) + 1
                x0 = int(coords[:, 1].min())
                x1 = int(coords[:, 1].max()) + 1
                pad_x = int(round((x1 - x0) * 0.35))
                pad_y = int(round((y1 - y0) * 0.45))
                x0 = max(x0 - pad_x, 0)
                y0 = max(y0 - pad_y, 0)
                x1 = min(x1 + pad_x, resized_width)
                y1 = min(y1 + pad_y, resized_height)
                if x1 - x0 >= 40 and y1 - y0 >= 25:
                    _append_focus_crop("focus", (x0, y0, x1, y1))

    reference_size = (resized_width, resized_height)

    def _iter_variant_entries() -> Iterable[Tuple[str, Image.Image, Tuple[int, int], Tuple[int, int]]]:
        for label, crop, offset in focus_variants:
            yield label, crop, offset, reference_size
        for label, variant in _iter_variants():
            if variant is None:
                continue
            yield label, variant, (0, 0), reference_size
    @dataclass
    class _OCRWord:
        text: str
        left: int
        top: int
        width: int
        height: int
        conf: Optional[float]

    def _group_paddle_words(raw_result) -> List[List[_OCRWord]]:
        words: List[_OCRWord] = []
        for entry in raw_result or []:
            if not entry:
                continue
            if isinstance(entry, dict):
                texts = entry.get("rec_texts") or []
                scores = entry.get("rec_scores") or []
                polys = entry.get("rec_polys") or entry.get("dt_polys") or []
                boxes = entry.get("rec_boxes")
                for idx, text in enumerate(texts):
                    token = (text or "").strip()
                    if not token:
                        continue
                    score = None
                    if idx < len(scores):
                        try:
                            score = float(scores[idx])
                        except (TypeError, ValueError):
                            score = None
                    points_seq = []
                    poly = None
                    if isinstance(polys, (list, tuple)):
                        if idx < len(polys):
                            poly = polys[idx]
                        elif polys:
                            poly = polys[0]
                    elif polys is not None:
                        poly = polys
                    if poly is not None:
                        try:
                            iterable = poly.tolist()
                        except AttributeError:
                            iterable = list(poly)
                        for pt in iterable or []:
                            if not pt:
                                continue
                            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                                try:
                                    points_seq.append([float(pt[0]), float(pt[1])])
                                except (TypeError, ValueError):
                                    continue
                    if not points_seq and boxes is not None:
                        box = None
                        if isinstance(boxes, (list, tuple)):
                            if idx < len(boxes):
                                box = boxes[idx]
                            elif boxes:
                                box = boxes[0]
                        else:
                            box = boxes
                        if box is not None:
                            try:
                                flat = box.tolist()
                            except AttributeError:
                                flat = list(box)
                            if len(flat) == 4:
                                x0, y0, x1, y1 = flat
                                points_seq = [
                                    [float(x0), float(y0)],
                                    [float(x1), float(y0)],
                                    [float(x1), float(y1)],
                                    [float(x0), float(y1)],
                                ]
                    if not points_seq:
                        continue
                    xs = [float(pt[0]) for pt in points_seq if pt]
                    ys = [float(pt[1]) for pt in points_seq if pt]
                    if not xs or not ys:
                        continue
                    left = min(xs)
                    top = min(ys)
                    right = max(xs)
                    bottom = max(ys)
                    width = max(int(round(right - left)), 0)
                    height = max(int(round(bottom - top)), 0)
                    conf = None
                    if score is not None and math.isfinite(score):
                        conf = float(score)
                    words.append(
                        _OCRWord(
                            text=token,
                            left=int(round(left)),
                            top=int(round(top)),
                            width=width,
                            height=height,
                            conf=conf,
                        )
                    )
                continue
            for detection in entry:
                if not detection:
                    continue
                try:
                    points, data = detection
                except (TypeError, ValueError):
                    continue
                if not data:
                    continue
                token = (data[0] or "").strip()
                if not token:
                    continue
                score = None
                if len(data) > 1:
                    try:
                        score = float(data[1])
                    except (TypeError, ValueError):
                        score = None
                xs = [float(pt[0]) for pt in (points or []) if pt]
                ys = [float(pt[1]) for pt in (points or []) if pt]
                if not xs or not ys:
                    continue
                left = min(xs)
                top = min(ys)
                right = max(xs)
                bottom = max(ys)
                width = max(int(round(right - left)), 0)
                height = max(int(round(bottom - top)), 0)
                conf = None
                if score is not None and math.isfinite(score):
                    conf = float(score)
                words.append(
                    _OCRWord(
                        text=token,
                        left=int(round(left)),
                        top=int(round(top)),
                        width=width,
                        height=height,
                        conf=conf,
                    )
                )
        if not words:
            return []
        words.sort(key=lambda w: (w.top, w.left))
        lines: List[List[_OCRWord]] = []
        for word in words:
            placed = False
            for line in lines:
                baseline = line[0]
                baseline_center = baseline.top + baseline.height / 2.0
                word_center = word.top + word.height / 2.0
                threshold = max(baseline.height, word.height, 1) * 0.6
                if abs(word_center - baseline_center) <= threshold:
                    line.append(word)
                    placed = True
                    break
            if not placed:
                lines.append([word])
        for line in lines:
            line.sort(key=lambda w: w.left)
        return lines

    seen_detections: Set[Tuple[float, int, int, int, int]] = set()
    seen_texts: Set[str] = set()

    def _extract_line_detections(
        words: Sequence[_OCRWord],
        variant_size: Tuple[int, int],
        *,
        offset: Tuple[int, int] = (0, 0),
        reference_size: Optional[Tuple[int, int]] = None,
    ) -> List[MicroscopeDetection]:
        detections: List[MicroscopeDetection] = []
        vw, vh = variant_size
        if vw <= 0 or vh <= 0:
            return detections
        ref_w, ref_h = reference_size or variant_size
        if ref_w <= 0 or ref_h <= 0:
            return detections
        scale_ref_x = ref_w / float(vw)
        scale_ref_y = ref_h / float(vh)
        offset_x, offset_y = offset
        normalised_words = [_normalise_microscope_text(word.text) for word in words]
        lowered_words = [text.lower().replace("Î¼", "Âµ") for text in normalised_words]
        for idx, word in enumerate(words):
            normalised = normalised_words[idx]
            marker_match = re.match(r"^\[\s*([12Il])\s*\]\s*", normalised)
            marker_offset = 0
            marker: Optional[int] = None
            if marker_match:
                marker_offset = marker_match.end()
                digit = marker_match.group(1)
                if digit in {"I", "l"}:
                    digit = "1"
                try:
                    marker = int(digit)
                except (TypeError, ValueError):
                    marker = None
            candidate_segment = normalised[marker_offset:]
            context_prefix = " ".join(normalised_words[max(0, idx - 3) : idx + 1])
            match = re.search(r"(\d+(?:[.,]\d+)?)", candidate_segment)
            if not match:
                continue
            raw_value = match.group(1).replace(",", ".")
            try:
                value = float(raw_value)
            except ValueError:
                continue
            if not math.isfinite(value) or value <= 0 or value > 1_000:
                continue
            suffix = normalised[marker_offset + match.end():].lower()
            unit_idx: Optional[int] = None
            if any(hint in suffix for hint in MICROSCOPE_UNIT_HINTS):
                unit_idx = idx
            else:
                for offset in range(1, 4):
                    probe_idx = idx + offset
                    if probe_idx >= len(words):
                        break
                    probe_text = lowered_words[probe_idx]
                    if any(hint in probe_text for hint in MICROSCOPE_UNIT_HINTS):
                        unit_idx = probe_idx
                        break
            if unit_idx is None and marker is not None:
                unit_idx = idx
            if marker is None:
                context_normalised = context_prefix.replace("Î¼", MICRO_SIGN)
                if any(hint in context_normalised for hint in ("[1", "1]", "[1]")):
                    marker = 1
                elif any(hint in context_normalised for hint in ("[2", "2]", "[2]")):
                    marker = 2
            if unit_idx is None:
                lookahead = " ".join(lowered_words[idx : min(len(words), idx + 3)])
                if any(hint in lookahead for hint in MICROSCOPE_UNIT_HINTS):
                    unit_idx = idx
            if unit_idx is None:
                continue
            start_idx = idx
            prefix = normalised[: marker_offset + match.start()]
            if any(hint in prefix for hint in ("[1", "1]", "[1]")) or "[1]" in normalised:
                marker = 1
            elif any(hint in prefix for hint in ("[2", "2]", "[2]")) or "[2]" in normalised:
                marker = 2
            if marker is None:
                for offset in range(1, 3):
                    prev_idx = idx - offset
                    if prev_idx < 0:
                        break
                    prev_text = normalised_words[prev_idx]
                    if any(hint in prev_text for hint in ("[1", "1]", "[1]")):
                        marker = 1
                        start_idx = min(start_idx, prev_idx)
                        break
                    if any(hint in prev_text for hint in ("[2", "2]", "[2]")):
                        marker = 2
                        start_idx = min(start_idx, prev_idx)
                        break
            tokens = words[start_idx : unit_idx + 1]
            if not tokens:
                continue
            left = min(entry.left for entry in tokens)
            top = min(entry.top for entry in tokens)
            right = max(entry.left + entry.width for entry in tokens)
            bottom = max(entry.top + entry.height for entry in tokens)
            left_ref = (left * scale_ref_x) + offset_x
            top_ref = (top * scale_ref_y) + offset_y
            right_ref = (right * scale_ref_x) + offset_x
            bottom_ref = (bottom * scale_ref_y) + offset_y
            left_base = max(int(round(left_ref * scale_to_original_x)), 0)
            top_base = max(int(round(top_ref * scale_to_original_y)), 0)
            right_base = min(
                int(round(right_ref * scale_to_original_x)), original_width
            )
            bottom_base = min(
                int(round(bottom_ref * scale_to_original_y)), original_height
            )
            if right_base <= left_base or bottom_base <= top_base:
                continue
            confidences = [
                entry.conf
                for entry in tokens
                if isinstance(entry.conf, (int, float)) and entry.conf >= 0
            ]
            confidence = None
            if confidences:
                confidence = float(sum(confidences) / len(confidences))
            detection = MicroscopeDetection(
                value=value,
                image_path=path,
                bbox=(left_base, top_base, right_base, bottom_base),
                text=" ".join(entry.text for entry in tokens),
                source="ocr",
                confidence=confidence,
                marker=marker,
            )
            detections.append(detection)
        return detections

    def _consume_ocr_output(
        label: str,
        raw_result,
        variant_size: Tuple[int, int],
        *,
        offset: Tuple[int, int] = (0, 0),
        reference_size: Optional[Tuple[int, int]] = None,
    ) -> bool:
        lines = _group_paddle_words(raw_result)
        if not lines:
            return False
        combined_lines: List[str] = []
        for line in lines:
            if not line:
                continue
            line_text_raw = " ".join(word.text for word in line)
            line_text = " ".join(line_text_raw.split())
            if not line_text:
                continue
            _append_candidate(line_text)
            tagged = f"{label}: {line_text}"
            if tagged not in seen_texts:
                result.texts.append(tagged)
                seen_texts.add(tagged)
            combined_lines.append(line_text)
            for detection in _extract_line_detections(
                line,
                variant_size,
                offset=offset,
                reference_size=reference_size,
            ):
                bbox = detection.bbox or (0, 0, 0, 0)
                key = (
                    round(detection.value, 2),
                    bbox[0],
                    bbox[1],
                    bbox[2],
                    bbox[3],
                )
                if key in seen_detections:
                    continue
                if detection.text:
                    detection.text = " ".join(str(detection.text).split())
                result.detections.append(detection)
                seen_detections.add(key)
                result.append_value(detection.value)
        if combined_lines:
            _append_candidate("\n".join(combined_lines))
            return True
        return False

    processed_any_variant = False
    if ocr is not None:
        try:
            direct_result = ocr.ocr(str(path))
        except Exception:
            direct_result = None
        if direct_result:
            if _consume_ocr_output("original", direct_result, (original_width, original_height)):
                processed_any_variant = True

        for label, variant, offset, ref_size in _iter_variant_entries():
            if variant is None:
                continue
            variant_size = variant.size
            variant_rgb = variant.convert("RGB")
            variant_array = np.array(variant_rgb)
            if variant_array.ndim == 2:
                variant_array = np.stack([variant_array] * 3, axis=-1)
            elif variant_array.ndim == 3 and variant_array.shape[2] == 4:
                variant_array = variant_array[:, :, :3]
            if variant_array.ndim != 3 or variant_array.shape[2] != 3:
                continue
            variant_array = variant_array.astype("uint8", copy=False)
            bgr_image = variant_array[:, :, ::-1].copy()
            try:
                ocr_result = ocr.ocr(bgr_image)
            except Exception:
                log.debug(
                    "PaddleOCR failed while processing %s; skipping this variant",
                    path,
                    exc_info=True,
                )
                continue
            if _consume_ocr_output(
                label,
                ocr_result,
                variant_size,
                offset=offset,
                reference_size=ref_size,
            ):
                processed_any_variant = True

            if candidates and any("[1" in candidate or "1]" in candidate for candidate in candidates):
                values = _parse_microscope_candidates(candidates)
                if values:
                    for value in values:
                        result.append_value(value)
                    break
            if result.values:
                break

    if not processed_any_variant:
        log.debug("PaddleOCR produced no candidate text for %s", path)

    if not result.values:
        parsed = _parse_microscope_candidates(candidates)
        for value in parsed:
            result.append_value(value)

    if not result.values and result.detections:
        seen_keys: Dict[float, float] = {}
        for detection in result.detections:
            key = round(detection.value, 2)
            seen_keys.setdefault(key, detection.value)
        result.values.extend(seen_keys.values())

    if not result.values:
        fallback_numbers: List[float] = []
        seen_numbers: Set[float] = set()
        pool: List[str] = []
        if candidates:
            pool.extend(candidates)
        if result.texts:
            pool.extend(result.texts)
        for raw_text in pool:
            if not raw_text:
                continue
            text = _normalise_microscope_text(raw_text)
            for match in re.finditer(r"\d+(?:[.,]\d+)?", text):
                start, end = match.start(), match.end()
                prefix = text[start - 1] if start > 0 else ""
                suffix = text[end] if end < len(text) else ""
                if prefix in "[{(" and suffix in "]})":
                    continue
                number = match.group(0).replace(",", ".")
                try:
                    value = float(number)
                except ValueError:
                    continue
                if not math.isfinite(value) or value <= 0 or value > 500:
                    continue
                key = round(value, 3)
                if key in seen_numbers:
                    continue
                seen_numbers.add(key)
                fallback_numbers.append(value)
        for value in fallback_numbers:
            result.append_value(value)

    if result.values:
        deduped: Dict[float, float] = {}
        for value in result.values:
            key = round(float(value), 2)
            deduped.setdefault(key, float(value))
        result.values = list(deduped.values())

    return result


class MicroscopeGroupingResult(tuple):
    """Combined grouping output that behaves like both tuple and mapping."""

    __slots__ = ()

    def __new__(
        cls,
        index: Dict[Tuple[str, int, int], MicroscopeMeasurements],
        cache: Dict[str, MicroscopeCacheEntry],
    ) -> "MicroscopeGroupingResult":
        return super().__new__(cls, (index, cache))

    @property
    def index(self) -> Dict[Tuple[str, int, int], MicroscopeMeasurements]:
        return super().__getitem__(0)

    @property
    def cache(self) -> Dict[str, MicroscopeCacheEntry]:
        return super().__getitem__(1)

    def __contains__(self, key: object) -> bool:
        return key in self.index

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, (int, slice)):
            return super().__getitem__(key)
        return self.index[key]

    def get(self, key, default=None):
        return self.index.get(key, default)

    def keys(self):
        return self.index.keys()

    def items(self):
        return self.index.items()

    def values(self):
        return self.index.values()

    def __repr__(self) -> str:
        return f"MicroscopeGroupingResult(index={self.index!r}, cache={self.cache!r})"


def _group_microscope_measurements(
    microscope_files: Sequence[Path],
    logger: Optional[logging.Logger],
    progress_callback: Optional[Callable[[int, int], None]] = None,
    debug_callback: Optional[Callable[[Path, MicroscopeOCRResult], None]] = None,
    update_callback: Optional[
        Callable[[Tuple[str, int, int], MicroscopeMeasurements], None]
    ] = None,
    cache: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[Tuple[str, int, int], MicroscopeMeasurements], Dict[str, MicroscopeCacheEntry]]:
    """Group microscope captures into microwire measurements while caching OCR output."""

    log = _logger(logger)
    combined: List[Path] = []
    seen: Set[Path] = set()
    for raw_path in microscope_files:
        path = Path(raw_path)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        combined.append(path)

    def _cache_key(path: Path) -> str:
        try:
            return str(path.resolve())
        except Exception:
            return str(path)

    cache_lookup: Dict[str, MicroscopeCacheEntry] = {}
    if cache:
        for key, payload in cache.items():
            entry: Optional[MicroscopeCacheEntry]
            if isinstance(payload, MicroscopeCacheEntry):
                entry = payload
            elif isinstance(payload, Mapping):
                entry = MicroscopeCacheEntry.from_dict(payload)
            else:
                entry = None
            if entry is None:
                continue
            cache_lookup[str(key)] = entry

    total = len(combined)
    processed = 0
    grouped: Dict[Tuple[str, int, int], MicroscopeMeasurements] = {}
    updated_cache: Dict[str, MicroscopeCacheEntry] = {}
    for raw_path in combined:
        path = raw_path.expanduser()
        processed += 1

        def _notify() -> None:
            if progress_callback is not None:
                try:
                    progress_callback(processed, total)
                except BuildCancelledError:
                    raise
                except Exception:
                    pass

        try:
            stat_result = path.stat()
        except OSError:
            log.debug("Microscope image %s does not exist; skipping", path)
            _notify()
            continue
        mtime = float(stat_result.st_mtime)
        size = int(stat_result.st_size)

        key = _microscope_key(path)
        if key is None:
            log.debug("Unable to derive microwire key from microscope image %s", path)
            _notify()
            continue
        category = _microscope_category(path)
        record = grouped.setdefault(key, MicroscopeMeasurements())

        cache_token = _cache_key(path)
        cache_entry = cache_lookup.get(cache_token) or cache_lookup.get(str(path))
        raw_result: MicroscopeOCRResult | Iterable[float] | None = None
        if cache_entry is not None and cache_entry.is_current(path):
            try:
                raw_result = cache_entry.to_result(path)
            except Exception:
                raw_result = None
        if raw_result is None:
            try:
                raw_result = _extract_microscope_diameters(path, log)
            except BuildCancelledError:
                raise
            except Exception:
                log.exception("Microscope OCR failed for %s", path)
                _notify()
                continue

        if isinstance(raw_result, MicroscopeOCRResult):
            result = MicroscopeOCRResult(
                values=list(raw_result.values),
                detections=list(raw_result.detections),
                texts=list(raw_result.texts),
            )
        else:
            values = [
                float(v)
                for v in raw_result
                if isinstance(v, (int, float)) and math.isfinite(float(v)) and float(v) > 0
            ]
            result = MicroscopeOCRResult(values=values)

        values = list(result.values)
        detections = list(result.detections)
        debug_texts = list(result.texts)

        if debug_callback is not None:
            debug_payload = MicroscopeOCRResult(
                values=list(result.values),
                detections=list(result.detections),
                texts=list(debug_texts),
            )
            try:
                debug_callback(path, debug_payload)
            except Exception:
                log.debug(
                    "Microscope OCR debug callback failed for %s", path, exc_info=True
                )

        if detections:
            grouped_detections: Dict[str, List[MicroscopeDetection]] = {}
            for detection in detections:
                override_category = category
                if category == "glass":
                    if detection.marker == 2:
                        continue
                    if detection.marker == 1:
                        override_category = "glass"
                else:
                    if detection.marker == 1:
                        override_category = "core"
                    elif detection.marker == 2:
                        override_category = "glass"
                grouped_detections.setdefault(override_category, []).append(detection)
            used_keys: Set[float] = set()
            for det_category, det_list in grouped_detections.items():
                numeric_values = [
                    float(det.value)
                    for det in det_list
                    if isinstance(det.value, (int, float))
                ]
                if not numeric_values:
                    for detection in det_list:
                        fallback_path = getattr(detection, "image_path", None) or path
                        record.add_placeholder(det_category, fallback_path)
                    continue
                record.extend(det_category, numeric_values, det_list)
                for numeric in numeric_values:
                    used_keys.add(round(float(numeric), 3))
            residual_values: List[float] = []
            for value in values:
                if not isinstance(value, (int, float)):
                    continue
                numeric = float(value)
                rounded = round(numeric, 3)
                if rounded in used_keys:
                    continue
                residual_values.append(numeric)
            if residual_values:
                record.extend(category, residual_values, [])
        elif values:
            record.extend(category, values, detections)
        else:
            record.add_placeholder(category, path)
            cache_entry_final = MicroscopeCacheEntry.from_result(path, mtime, size, result)
            cache_entry_final.path = cache_token
            updated_cache[cache_token] = cache_entry_final
            _notify()
            continue
        if not detections and values:
            record.add_placeholder(category, path)
        if update_callback is not None:
            try:
                update_callback(key, record)
            except Exception:
                log.debug(
                    "Microscope OCR debug callback failed for %s", key, exc_info=True
                )

        cache_entry_final = MicroscopeCacheEntry.from_result(path, mtime, size, result)
        cache_entry_final.path = cache_token
        updated_cache[cache_token] = cache_entry_final
        _notify()

    missing_references: List[str] = []
    mismatched_references: List[str] = []
    for key, override in MANUAL_DIAMETER_OVERRIDES.items():
        record = grouped.get(key)
        d_expected = override.get("d")
        D_expected = override.get("D")
        d_actual = record.best_core() if record else None
        D_actual = record.best_glass() if record else None
        comp, draw, piece = key
        label = f"{comp} {draw}/{piece}" if piece is not None else f"{comp} {draw}"
        if d_expected is not None:
            if d_actual is None:
                missing_references.append(f"{label} d={d_expected}")
            elif abs(d_actual - d_expected) > 0.5:
                mismatched_references.append(
                    f"{label} d expected {d_expected:.2f}µm got {d_actual:.2f}µm"
                )
        if D_expected is not None:
            if D_actual is None:
                missing_references.append(f"{label} D={D_expected}")
            elif abs(D_actual - D_expected) > 0.5:
                mismatched_references.append(
                    f"{label} D expected {D_expected:.2f}µm got {D_actual:.2f}µm"
                )
    if missing_references:
        preview = ", ".join(missing_references[:6])
        if len(missing_references) > 6:
            preview += ", …"
        log.info(
            "Microscope OCR is still missing %s manual reference(s): %s",
            len(missing_references),
            preview,
        )
    if mismatched_references:
        preview = ", ".join(mismatched_references[:6])
        if len(mismatched_references) > 6:
            preview += ", …"
        log.warning(
            "Microscope OCR deviates from manual references: %s", preview
        )
    return MicroscopeGroupingResult(grouped, updated_cache)

def _draw_key(path: Path) -> Optional[Tuple[str, int]]:
    candidates: List[str] = [parent.name for parent in path.parents]
    candidates.extend([path.stem, path.name])
    for candidate in candidates:
        if not candidate:
            continue
        composition = _extract_composition_token(candidate)
        if not composition:
            continue
        draw_match = re.search(r"(\d+)", candidate)
        if not draw_match:
            continue
        try:
            draw_x = int(draw_match.group(1))
        except (TypeError, ValueError):
            continue
        return composition, draw_x
    return None


def _collect_video_metrics(
    video_files: Sequence[Path],
    logger: Optional[logging.Logger],
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Dict[Tuple[str, int, Optional[int]], VideoMetricsSummary]:
    log = _logger(logger)
    aggregated: Dict[Tuple[str, int, Optional[int]], VideoMetricsSummary] = {}
    unique_files = list(dict.fromkeys(Path(p) for p in video_files))
    total = len(unique_files)
    processed = 0
    for raw_path in unique_files:
        path = raw_path.expanduser()
        processed += 1

        def _notify() -> None:
            if progress_callback is not None:
                try:
                    progress_callback(processed, total)
                except BuildCancelledError:
                    raise
                except Exception:
                    pass

        try:
            if not path.exists():
                log.debug("Video file %s does not exist; skipping", path)
                _notify()
                continue
        except OSError:
            _notify()
            continue
        key = _microscope_key(path)
        if key is not None:
            composition, draw_x, piece_y = key
        else:
            draw_key = _draw_key(path)
            if draw_key is None:
                log.debug("Unable to derive microwire key from video %s", path)
                _notify()
                continue
            composition, draw_x = draw_key
            piece_y = None
        try:
            result = extract_video_metrics(path, logger=log)
        except Exception:
            log.exception("Failed to analyse video %s", path)
            _notify()
            continue
        summary = aggregated.setdefault((composition, draw_x, piece_y), VideoMetricsSummary())
        summary.record(result, source=path)
        _notify()
    return aggregated


def _parse_draw_rows(
    df: pd.DataFrame,
    headers: List[Optional[str]],
    composition: str,
    index: FabricationIndex,
    logger: logging.Logger,
    source_path: Path,
) -> None:
    seen_draws: set[int] = set()
    for _, row in df.iterrows():
        first_cell = row.iloc[0] if len(row) else None
        if first_cell is None or _is_nan(first_cell):
            continue
        text = _normalise_text(first_cell)
        if not text:
            continue
        draw_x: Optional[int] = None
        m = DRAW_PATTERN.match(text)
        if m:
            draw_x = int(m.group("draw"))
        elif text.lower().startswith(composition.lower()):
            draw_x = 1
        if draw_x is None:
            continue
        if draw_x in seen_draws:
            logger.debug("Duplicate draw %s in %s", draw_x, composition)
        seen_draws.add(draw_x)
        record: Dict[str, object] = {"_source_path": str(source_path)}
        for col_idx, field in enumerate(headers):
            if col_idx == 0 or not field:
                continue
            value = row.iloc[col_idx] if col_idx < len(row) else None
            parsed, raw = _extract_field_value(field, value)
            record[field] = parsed
            if field in RAW_VALUE_FIELDS:
                record[f"{field}_raw"] = raw
            _append_dimension_display(record, field, parsed, raw)
        index.set_draw(composition, draw_x, record)


def _parse_piece_rows(
    df: pd.DataFrame,
    headers: List[Optional[str]],
    composition: str,
    draw_x: Optional[int],
    index: FabricationIndex,
    logger: logging.Logger,
    source_path: Path,
) -> None:
    if draw_x is None:
        logger.warning("Could not determine draw number for piece workbook %s", composition)
        return
    for _, row in df.iterrows():
        first_cell = row.iloc[0] if len(row) else None
        if first_cell is None or _is_nan(first_cell):
            continue
        text = _normalise_text(first_cell)
        if not text:
            continue
        m = PIECE_PATTERN.match(text)
        if not m:
            continue
        piece_y = int(m.group("piece"))
        record: Dict[str, object] = {"_source_path": str(source_path)}
        for col_idx, field in enumerate(headers):
            if col_idx == 0 or not field:
                continue
            value = row.iloc[col_idx] if col_idx < len(row) else None
            parsed, raw = _extract_field_value(field, value)
            record[field] = parsed
            if field in RAW_VALUE_FIELDS:
                record[f"{field}_raw"] = raw
            _append_dimension_display(record, field, parsed, raw)
        index.set_piece(composition, draw_x, piece_y, record)


def _read_excel(path: Path, logger: Optional[logging.Logger] = None) -> pd.DataFrame:
    try:
        return pd.read_excel(path, header=None, dtype=object)
    except ImportError:
        raise
    except ValueError as exc:
        engines: List[str] = []
        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            engines.append("openpyxl")
        if suffix in {".xls", ".xlsb"}:
            engines.append("xlrd")
        engines.extend(["openpyxl", "xlrd", "odf"])
        for engine in engines:
            try:
                return pd.read_excel(path, header=None, dtype=object, engine=engine)
            except ImportError:
                continue
            except ValueError:
                continue
            except Exception:
                continue
        if logger is not None:
            logger.warning(
                "%s: unable to determine Excel format (%s); skipping",
                path,
                exc,
            )
        return pd.DataFrame()
    except Exception as exc:
        if logger is not None:
            logger.exception("Failed to read %s", path)
        return pd.DataFrame()


def _parse_composition_workbook(path: Path, index: FabricationIndex, logger: logging.Logger) -> None:
    df = _read_excel(path, logger)
    if df.empty:
        logger.warning("%s is empty", path)
        return
    header_values = _merged_header_row(df, 0)
    headers = [_header_key(value) for value in header_values]
    data = df.iloc[1:].reset_index(drop=True)
    composition = _composition_from_path(path)
    _parse_draw_rows(data, headers, composition, index, logger, path)


def _is_piece_header_row(values: Sequence[object]) -> bool:
    tokens: List[str] = []
    collapsed: List[str] = []
    for value in values:
        text = _normalise_text(value)
        if not text:
            continue
        ascii_text = unicodedata.normalize("NFKD", text)
        ascii_text = "".join(ch for ch in ascii_text if not unicodedata.combining(ch)).lower()
        tokens.append(ascii_text)
        collapsed.append(re.sub(r"[^a-z0-9]", "", ascii_text))
    if not tokens:
        return False
    collapsed_set = {token for token in collapsed if token}
    token_set = {token for token in tokens if token}
    score = 0
    if any(token.startswith("p") and len(token) <= 3 for token in collapsed_set):
        score += 1
    if any("datum" in token for token in token_set):
        score += 1
    if any("dlka" in token or "dka" in token or "dlzka" in token for token in token_set):
        score += 1
    if any(token in {"dum", "d", "dmu"} or "dum" in token for token in collapsed_set):
        score += 1
    if any("d/d" in token or "d/" in token for token in token_set) or "dd" in collapsed_set:
        score += 1
    if any("intenzita" in token for token in token_set):
        score += 1
    if any("hsw" in token for token in token_set):
        score += 1
    return score >= 3


def _parse_piece_workbook(path: Path, index: FabricationIndex, logger: logging.Logger) -> None:
    df = _read_excel(path, logger)
    if df.empty:
        logger.warning("%s is empty", path)
        return
    best_idx: Optional[int] = None
    best_headers: List[Optional[str]] = []
    best_score = -1
    for idx, row in df.iterrows():
        if not _is_piece_header_row(row.tolist()):
            continue
        candidate_values = _merged_header_row(df, idx)
        candidate_headers = [_header_key(value) for value in candidate_values]
        score = sum(1 for field in candidate_headers if field)
        if score > best_score:
            best_idx = idx
            best_headers = candidate_headers
            best_score = score
    if best_idx is None:
        if len(df.index) > 1:
            best_idx = 1
            logger.warning("%s: unable to locate header row; using the second row as a fallback", path)
            candidate_values = _merged_header_row(df, best_idx)
            best_headers = [_header_key(value) for value in candidate_values]
        else:
            logger.warning("%s: unable to locate header row", path)
            return
    header_idx = best_idx
    header_values = _merged_header_row(df, header_idx)
    if not best_headers:
        best_headers = [_header_key(value) for value in header_values]
    headers = best_headers
    data = df.iloc[header_idx + 1 :].reset_index(drop=True)
    stem = path.stem
    match = re.search(r"(?P<draw>\d+)[._](?P<comp>[A-Za-z0-9]+)", stem)
    draw_x: Optional[int] = None
    composition = _composition_from_path(path)
    if match:
        draw_x = int(match.group("draw"))
        composition = match.group("comp")
    _parse_piece_rows(data, headers, composition, draw_x, index, logger, path)


def build_fabrication_index(
    fabrication_files: Sequence[Path],
    logger: Optional[logging.Logger] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
) -> FabricationIndex:
    log = _logger(logger)
    index = FabricationIndex()
    unique_files = list(dict.fromkeys(Path(p) for p in fabrication_files))
    total = len(unique_files)
    for idx, path in enumerate(unique_files, start=1):
        if cancel_callback is not None and cancel_callback():
            raise BuildCancelledError()
        if path.suffix.lower() != ".xlsx":
            log.debug("Skipping non-Excel file %s", path)
            if progress_callback is not None:
                try:
                    progress_callback(idx, total)
                except Exception:
                    pass
            continue
        stem = path.stem
        parent_stem = path.parent.name
        if re.match(r"^\d+", stem) or re.match(r"^\d+", parent_stem):
            _parse_piece_workbook(path, index, log)
        else:
            _parse_composition_workbook(path, index, log)
        if progress_callback is not None:
            try:
                progress_callback(idx, total)
            except Exception:
                pass
    return index


@dataclass
class MeasurementMetadata:
    composition_token: str
    draw_x: Optional[int]
    piece_y: Optional[int]
    setpoint_mA: Optional[int]
    alt_variant: bool
    measurement_id: str
    file_name: str
    relpath: str
    timestamp_mtime_utc: str


@dataclass
class MeasurementRecord:
    path: Path
    metadata: MeasurementMetadata
    dataframe: pd.DataFrame
    sanity_ok: bool
    sanity_error: Optional[float]


def _hash_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _metadata_from_path(path: Path, root: Optional[Path] = None) -> MeasurementMetadata:
    stat = path.stat()
    timestamp = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    base = path.stem
    parts = base.split()
    composition = parts[0] if parts else base
    xy_match = XY_PATTERN.search(base)
    draw_x: Optional[int] = int(xy_match.group(1)) if xy_match else None
    piece_y: Optional[int] = int(xy_match.group(2)) if xy_match else None
    setpoint_match = SETPOINT_PATTERN.search(base)
    setpoint = int(setpoint_match.group(1)) if setpoint_match else None
    alt_variant = bool(ALT_VARIANT_PATTERN.search(base))
    relpath = os.fspath(path.relative_to(root)) if root and path.is_relative_to(root) else path.as_posix()
    return MeasurementMetadata(
        composition_token=composition,
        draw_x=draw_x,
        piece_y=piece_y,
        setpoint_mA=setpoint,
        alt_variant=alt_variant,
        measurement_id=_hash_file(path),
        file_name=path.name,
        relpath=relpath,
        timestamp_mtime_utc=timestamp,
    )


def _load_annealing(
    path: Path,
    *,
    expected_setpoint_mA: Optional[float] = None,
) -> pd.DataFrame:
    try:
        df = pd.read_csv(path, sep=None, engine="python", names=ANNEALING_COLUMNS, header=None)
    except (csv.Error, pd.errors.ParserError):
        df = pd.read_csv(
            path,
            sep=r"\s+",
            engine="python",
            names=ANNEALING_COLUMNS,
            header=None,
        )
    df = df.iloc[:, :3].copy()
    for column in ANNEALING_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["I_A", "R_ohm"]).reset_index(drop=True)
    currents = df["I_A"].to_numpy(dtype=float)
    finite = currents[np.isfinite(currents)]
    scale = 1.0
    if finite.size:
        max_abs = float(np.nanmax(np.abs(finite)))
        median_abs = float(np.nanmedian(np.abs(finite)))
        if expected_setpoint_mA and expected_setpoint_mA > 0:
            expected_amp = expected_setpoint_mA / 1000.0
            if expected_amp > 0 and max_abs > expected_amp * 5:
                scale = 1e-3
        if scale == 1.0:
            if max_abs > 500:
                scale = 1e-3
            elif median_abs > 10:
                scale = 1e-3
    scaled_currents = currents * scale
    df.loc[:, "I_A"] = scaled_currents
    df.loc[:, "I_mA"] = scaled_currents * 1_000.0

    try:
        from plotting.plugins.current_annealing.burnthrough import trim_burnthrough_glitch
    except ImportError:
        return df

    currents_source = "I_mA" if "I_mA" in df.columns else "I_A"
    currents_mA = df[currents_source].to_numpy(dtype=float)
    if currents_source == "I_A":
        currents_mA = currents_mA * 1e3
    resistances = df["R_ohm"].to_numpy(dtype=float)
    trimmed_currents, trimmed_resistances = trim_burnthrough_glitch(currents_mA, resistances)
    trimmed_count = int(trimmed_currents.shape[0])
    if trimmed_count < currents_mA.shape[0]:
        df = df.iloc[:trimmed_count].copy()
        df.loc[:, "I_mA"] = trimmed_currents
        df.loc[:, "I_A"] = trimmed_currents / 1e3
        df.loc[:, "R_ohm"] = trimmed_resistances
        df = df.reset_index(drop=True)
    return df


def _series_to_mA(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    try:
        max_abs = float(numeric.abs().max(skipna=True) or 0.0)
    except Exception:
        max_abs = 0.0
    if max_abs <= 50:
        return numeric * 1e3
    return numeric


def _resistance_sanity_check(df: pd.DataFrame) -> Tuple[bool, Optional[float]]:
    currents = df["I_A"].to_numpy(dtype=float)
    voltages = df["V_V"].to_numpy(dtype=float)
    resistances = df["R_ohm"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        expected = np.divide(voltages, currents, out=np.full_like(resistances, np.nan), where=currents != 0)
    denom = np.maximum(np.abs(resistances), 1e-9)
    rel_error = np.abs(expected - resistances) / denom
    finite = rel_error[np.isfinite(rel_error)]
    if finite.size == 0:
        return False, None
    mean_error = float(np.mean(finite))
    return mean_error < R_CHECK_THRESHOLD, mean_error


def _value_for_output(record: Dict[str, object], field: str) -> Optional[object]:
    if not record:
        return None
    value = record.get(field)
    if value is None or _is_nan(value):
        raw = record.get(f"{field}_raw")
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None
    return value


def _compose_notes(*records: Dict[str, object]) -> Optional[str]:
    notes: List[str] = []
    seen: set[str] = set()
    for record in records:
        if not record:
            continue
        for key in ("bistable_status", "notes"):
            candidate = _value_for_output(record, key)
            if candidate is None:
                continue
            text = str(candidate).strip()
            if not text:
                continue
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            notes.append(text)
    return "; ".join(notes) if notes else None


def _load_strain_records(
    paths: Sequence[Path],
    log: logging.Logger,
) -> Dict[Tuple[str, int, int], StrainRecord]:
    records: Dict[Tuple[str, int, int], StrainRecord] = {}
    if not paths:
        return records
    seen: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if path in seen:
            continue
        seen.add(path)
        if not path.exists():
            log.warning("Strain worksheet %s not found; skipping", path)
            continue
        try:
            df = pd.read_excel(path)
        except Exception:
            log.exception("Failed to read strain worksheet %s", path)
            continue
        if df.empty:
            continue
        column_map: Dict[str, int] = {}
        for idx, header in enumerate(df.columns):
            key = _clean_str(header).lower()
            if not key:
                continue
            if "composition" in key:
                column_map.setdefault("composition", idx)
            elif "microwire" in key or "wire" in key:
                column_map.setdefault("microwire", idx)
            elif key.startswith("m") and "length" in key:
                column_map.setdefault("m_length", idx)
            elif key.startswith("a") and "length" in key:
                column_map.setdefault("a_length", idx)
            elif "strain" in key or "%" in key:
                column_map.setdefault("strain", idx)
            elif "broke" in key or "status" in key or "note" in key:
                column_map.setdefault("status", idx)
        comp_idx = column_map.get("composition")
        micro_idx = column_map.get("microwire")
        if comp_idx is None or micro_idx is None:
            log.warning("%s is missing composition or microwire columns; skipping", path)
            continue
        for row in df.itertuples(index=False, name=None):
            values = list(row)
            composition = _clean_str(values[comp_idx])
            if not composition or composition.lower() == "composition":
                continue
            microwire_label = _clean_str(values[micro_idx])
            if not microwire_label:
                continue
            draw_piece = _microwire_tuple_from_label(microwire_label)
            if not draw_piece:
                continue
            draw, piece = draw_piece
            m_length = (
                _parse_strain_float(values[column_map["m_length"]])
                if "m_length" in column_map
                else None
            )
            a_length = (
                _parse_strain_float(values[column_map["a_length"]])
                if "a_length" in column_map
                else None
            )
            percent = (
                _parse_strain_float(values[column_map["strain"]])
                if "strain" in column_map
                else None
            )
            broke = False
            status_idx = column_map.get("status")
            if status_idx is not None:
                if _clean_str(values[status_idx]).lower() == "broke":
                    broke = True
            if not broke:
                for value in values:
                    if _clean_str(value).lower() == "broke":
                        broke = True
                        break
            if percent is None and not broke and m_length not in (None, 0) and a_length is not None:
                try:
                    percent = ((m_length - a_length) / m_length) * 100 if m_length else None
                except ZeroDivisionError:
                    percent = None
                if percent is not None and not math.isfinite(percent):
                    percent = None
            record = StrainRecord(
                composition=composition,
                draw=draw,
                piece=piece,
                microwire_label=microwire_label,
                m_length=m_length,
                a_length=a_length,
                percent=percent,
                broke=broke,
                source=path,
            )
            records[(composition, draw, piece)] = record
    return records


def _microwire_label(draw_x: Optional[int], piece_y: Optional[int]) -> str:
    if draw_x is None or piece_y is None:
        return ""
    return f"{draw_x}/{piece_y}"


def _select_high_measurement(records: List[MeasurementRecord]) -> Optional[MeasurementRecord]:
    if not records:
        return None

    def key(record: MeasurementRecord) -> Tuple[int, int, int, str]:
        setpoint = record.metadata.setpoint_mA
        priority_exact = 1 if setpoint == 1000 else 0
        magnitude = setpoint if setpoint is not None else -1
        variant_score = 1 if not record.metadata.alt_variant else 0
        return (priority_exact, magnitude, variant_score, record.metadata.file_name.lower())

    return max(records, key=key)


def _select_low_measurement(records: List[MeasurementRecord]) -> Optional[MeasurementRecord]:
    if not records:
        return None
    candidates = [r for r in records if r.metadata.setpoint_mA is not None]
    if not candidates:
        return None

    def key(record: MeasurementRecord) -> Tuple[int, int, str]:
        setpoint = record.metadata.setpoint_mA or 0
        variant_penalty = 0 if not record.metadata.alt_variant else 1
        return (setpoint, variant_penalty, record.metadata.file_name.lower())

    return min(candidates, key=key)


def _plot_measurement_matplotlib(
    df: pd.DataFrame,
    source: Path,
    plot_dir: Path,
    figsize: Tuple[float, float],
) -> Path:
    import matplotlib

    try:
        matplotlib.use("Agg", force=True)
    except Exception:
        pass
    import matplotlib.pyplot as plt

    matplotlib.rcParams["figure.max_open_warning"] = 0

    from plotting.plugins.current_annealing.core import plot_one
    from plotting.shared.toolkit import format_annealing_title

    plot_dir.mkdir(parents=True, exist_ok=True)
    title = format_annealing_title(source.stem)
    if "I_mA" in df.columns:
        currents = pd.to_numeric(df["I_mA"], errors="coerce")
    else:
        currents = _series_to_mA(df["I_A"])
    plot_df = pd.DataFrame({"I_mA": currents, "R_Ohm": pd.to_numeric(df["R_ohm"], errors="coerce")}).dropna()
    dpi_target = 192
    target_px = (
        max(int(figsize[0] * dpi_target), 200),
        max(int(figsize[1] * dpi_target), 120),
    )
    fig, fname = plot_one(plot_df, title, target_px=target_px)
    safe_stem = _safe_plot_stem(fname)
    plot_path = plot_dir / f"{safe_stem}.png"
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(plot_path, dpi=300)
    plt.close(fig)
    return plot_path


def _update_existing_csv_with_strain(
    path: Path,
    strain_records: Dict[Tuple[str, int, int], StrainRecord],
    output_columns: Sequence[str],
    log: logging.Logger,
) -> None:
    try:
        df = pd.read_csv(path)
    except Exception:
        log.exception("Failed to read existing CSV at %s; skipping update", path)
        return
    if df.empty:
        return
    if STRAIN_COLUMN not in df.columns:
        insert_index = len(df.columns)
        if STRAIN_COLUMN in output_columns:
            insert_index = list(output_columns).index(STRAIN_COLUMN)
        df.insert(insert_index, STRAIN_COLUMN, None)
    if {"Composition", "Microwire"}.issubset(df.columns) and strain_records:
        for idx, row in df.iterrows():
            composition = _clean_str(row.get("Composition"))
            microwire_label = _clean_str(row.get("Microwire"))
            key = _microwire_tuple_from_label(microwire_label)
            if not composition or not key:
                continue
            record = strain_records.get((composition, key[0], key[1]))
            if record is None:
                continue
            df.at[idx, STRAIN_COLUMN] = _format_strain_value(record)
    desired = [column for column in output_columns if column in df.columns]
    for column in df.columns:
        if column not in desired:
            desired.append(column)
    df = df[desired]
    df.to_csv(path, index=False)


def _update_existing_excel_with_strain(
    path: Path,
    strain_records: Dict[Tuple[str, int, int], StrainRecord],
    log: logging.Logger,
) -> None:
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("openpyxl is required to update Excel exports") from exc

    wb = load_workbook(path)
    ws = cast(Any, wb.active)
    if ws.max_row < 1:
        wb.save(path)
        return

    def _headers() -> list[str]:
        return [str(cell.value) if cell.value is not None else "" for cell in ws[1]]

    headers = _headers()
    try:
        d_over_d_index = headers.index("d/D") + 1
    except ValueError:
        d_over_d_index = None
    strain_target = len(headers) + 1 if d_over_d_index is None else d_over_d_index + 1
    if d_over_d_index is not None:
        for column_name in FIGURE_COLUMNS:
            headers = _headers()
            if column_name not in headers:
                continue
            current_index = headers.index(column_name) + 1
            if current_index != strain_target:
                offset = strain_target - current_index
                col_letter = get_column_letter(current_index)
                ws.move_range(
                    f"{col_letter}1:{col_letter}{ws.max_row}",
                    rows=0,
                    cols=offset,
                )
            strain_target += 1

    headers = _headers()
    if STRAIN_COLUMN in headers:
        current_index = headers.index(STRAIN_COLUMN) + 1
        if current_index != strain_target:
            offset = strain_target - current_index
            col_letter = get_column_letter(current_index)
            ws.move_range(
                f"{col_letter}1:{col_letter}{ws.max_row}",
                rows=0,
                cols=offset,
            )
    else:
        ws.insert_cols(strain_target)
        ws.cell(row=1, column=strain_target).value = STRAIN_COLUMN

    headers = _headers()
    try:
        composition_index = headers.index("Composition") + 1
        microwire_index = headers.index("Microwire") + 1
        strain_index = headers.index(STRAIN_COLUMN) + 1
    except ValueError:
        log.warning("Unable to locate Composition/Microwire columns in %s; skipping strain update", path)
        wb.save(path)
        return

    if not strain_records:
        wb.save(path)
        return

    for row_idx in range(2, ws.max_row + 1):
        composition = _clean_str(ws.cell(row=row_idx, column=composition_index).value)
        microwire_label = _clean_str(ws.cell(row=row_idx, column=microwire_index).value)
        key = _microwire_tuple_from_label(microwire_label)
        if not composition or not key:
            continue
        record = strain_records.get((composition, key[0], key[1]))
        if record is None:
            continue
        ws.cell(row=row_idx, column=strain_index).value = _format_strain_value(record)

    wb.save(path)


def _plot_measurement_origin(
    df: pd.DataFrame,
    source: Path,
    origin_dir: Path,
    log: Optional[logging.Logger] = None,
) -> Optional[OriginArtifact]:
    try:
        from plotting.plugins.current_annealing.core import plot_one_origin
        from plotting.shared.utils import format_annealing_title, schedule_origin_release
    except ImportError as exc:  # pragma: no cover - depends on optional module
        raise RuntimeError("originpro is not available") from exc

    if "I_mA" in df.columns:
        currents = pd.to_numeric(df["I_mA"], errors="coerce")
    else:
        currents = _series_to_mA(df["I_A"])
    plot_df = pd.DataFrame({"I_mA": currents, "R_Ohm": pd.to_numeric(df["R_ohm"], errors="coerce")}).dropna()
    title = format_annealing_title(source.stem)
    handles = plot_one_origin(
        plot_df,
        title,
        source.name,
        return_handles=True,
    )
    try:
        schedule_origin_release()
    except Exception:
        pass

    if not isinstance(handles, dict):
        return None

    description, graph_name, workbook_name, worksheet_name = _describe_origin_handles(handles)
    safe_stem = _safe_plot_stem(source.stem)
    origin_dir.mkdir(parents=True, exist_ok=True)
    artifact_name = f"{safe_stem}.oggu"
    target_path = origin_dir / artifact_name
    exported_path = _export_origin_object(handles, target_path, log)

    return OriginArtifact(
        descriptor=artifact_name,
        object_path=exported_path,
        graph_name=graph_name,
        workbook_name=workbook_name,
        worksheet_name=worksheet_name,
        display_text=description,
    )


def _describe_origin_handles(
    handles: Dict[str, object],
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    graph = handles.get("graph")
    workbook = handles.get("workbook")
    worksheet = handles.get("worksheet")
    legend_label = handles.get("legend_label")

    parts: list[str] = []
    if legend_label:
        parts.append(str(legend_label))
    graph_name = _origin_object_name(graph)
    if graph_name:
        parts.append(f"Graph: {graph_name}")
    workbook_name = _origin_object_name(workbook)
    if workbook_name:
        parts.append(f"Book: {workbook_name}")
    worksheet_name = _origin_object_name(worksheet)
    if worksheet_name:
        parts.append(f"Sheet: {worksheet_name}")
    description = " | ".join(parts) if parts else None
    return description, graph_name, workbook_name, worksheet_name


def _export_origin_object(
    handles: Dict[str, object], target_path: Path, log: Optional[logging.Logger]
) -> Optional[Path]:
    origin_any = handles.get("origin")
    graph = handles.get("graph")
    if origin_any is None or graph is None:
        return None

    target_path.parent.mkdir(parents=True, exist_ok=True)
    attempted: list[str] = []

    def _path_created() -> Optional[Path]:
        candidates = [
            target_path,
            target_path.with_suffix(".oggu"),
            target_path.with_suffix(".opju"),
        ]
        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate
            except OSError:
                continue
        return None

    for attr in ("save_copy", "save_as", "save"):
        method = getattr(graph, attr, None)
        if callable(method):
            try:
                method(str(target_path))
                created = _path_created()
                if created is not None:
                    return created
            except Exception:
                attempted.append(attr)

    for attr in ("save_page", "save_window"):
        method = getattr(origin_any, attr, None)
        if callable(method):
            try:
                method(str(target_path))
                created = _path_created()
                if created is not None:
                    return created
            except Exception:
                attempted.append(attr)

    lt_exec = getattr(origin_any, "lt_exec", None)
    if callable(lt_exec):
        graph_name = _origin_object_name(graph) or ""
        base = str(target_path)
        commands = []
        if graph_name:
            commands.append(f"page -s \"{graph_name}\"; save -oggu \"{base}\";")
            commands.append(f"page -s \"{graph_name}\"; save -opju \"{base}\";")
        commands.append(f"save -oggu \"{base}\";")
        commands.append(f"save -opju \"{base}\";")
        for command in commands:
            try:
                lt_exec(command)
                created = _path_created()
                if created is not None:
                    return created
            except Exception:
                attempted.append(command)

    if log is not None:
        log.warning(
            "Origin graph export failed for %s; attempted %s",
            target_path,
            ", ".join(attempted) if attempted else "no-export",
        )
    return None


def _embed_plots_in_excel_openpyxl(
    excel_path: Path,
    dataframe: pd.DataFrame,
    plot_name_to_path: Dict[str, Path],
    plot_dir: Path,
    log: logging.Logger,
    figure_size: Tuple[float, float],
    *,
    microscope_columns: Sequence[str] = (),
    microscope_crops: Optional[Dict[str, Path]] = None,
    highlight_map: Optional[Dict[str, Set[int]]] = None,
) -> None:
    """Insert Matplotlib plot images directly into the Excel export."""

    if not excel_path.exists():
        return
    if dataframe.empty:
        return
    figure_columns = [column for column in FIGURE_COLUMNS if column in dataframe.columns]
    microscope_columns = [
        column for column in microscope_columns if column in dataframe.columns
    ]
    if not figure_columns and not microscope_columns:
        return

    try:
        from openpyxl import load_workbook
        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.utils import get_column_letter
        from openpyxl.styles import PatternFill
    except ImportError:  # pragma: no cover - optional dependency guard
        log.warning("openpyxl is required to embed plots into Excel workbooks")
        return

    available: Dict[str, Path] = {}
    for name, path in plot_name_to_path.items():
        if path.exists():
            available[name] = path
    if plot_dir and plot_dir.exists():
        for candidate in plot_dir.glob("*.png"):
            available.setdefault(candidate.name, candidate)
    if not available:
        return

    workbook = load_workbook(excel_path)
    try:
        worksheet = workbook.active

        inserted = False
        reset_df = dataframe.reset_index(drop=True)
        target_width_in, target_height_in = figure_size
        target_width_px, target_height_px = _excel_pixel_limits(figure_size)
        row_height_pts = _excel_row_height(target_height_in)
        column_width_chars = _excel_column_width(target_width_in)
        for row_idx, row in reset_df.iterrows():
            for column in figure_columns:
                value = row.get(column)
                if not isinstance(value, str):
                    continue
                name = value.strip()
                if not name:
                    continue
                image_path = available.get(name)
                if image_path is None or not image_path.exists():
                    candidate = plot_dir / name if plot_dir else None
                    if candidate is None or not candidate.exists():
                        continue
                    available[name] = candidate
                    image_path = candidate
                width_px, height_px, dpi_x, dpi_y = _image_metrics(image_path)

                try:
                    image = XLImage(str(image_path))
                except Exception:
                    log.exception("Failed to load plot image %s for Excel export", image_path)
                    continue

                if width_px and target_width_px:
                    desired_width_px = int(round(target_width_px))
                    if not math.isclose(width_px, desired_width_px, rel_tol=1e-3):
                        image.width = desired_width_px
                if height_px and target_height_px:
                    desired_height_px = int(round(target_height_px))
                    if not math.isclose(height_px, desired_height_px, rel_tol=1e-3):
                        image.height = desired_height_px

                location = dataframe.columns.get_loc(column)
                if isinstance(location, slice):
                    continue
                if isinstance(location, np.ndarray):
                    continue
                try:
                    column_index = int(location) + 1
                except Exception:
                    continue
                row_number = row_idx + 2  # account for the header row
                column_letter = get_column_letter(column_index)
                cell_reference = f"{column_letter}{row_number}"

                # Clear the textual filename and embed the image anchored at the cell.
                worksheet[cell_reference].value = None
                worksheet.add_image(image, cell_reference)

                if row_height_pts > 0:
                    row_dim = worksheet.row_dimensions[row_number]
                    row_dim.height = row_height_pts
                    row_dim.customHeight = True

                if column_width_chars > 0:
                    col_dim = worksheet.column_dimensions[column_letter]
                    col_dim.width = column_width_chars
                    col_dim.customWidth = True

                inserted = True

            for column in microscope_columns:
                value = row.get(column)
                if not isinstance(value, str):
                    continue
                key = value.strip()
                if not key:
                    continue
                image_path = None
                if microscope_crops:
                    image_path = microscope_crops.get(key)
                if image_path is None:
                    candidate = Path(key)
                    if candidate.exists():
                        image_path = candidate
                if image_path is None or not image_path.exists():
                    continue
                try:
                    image = XLImage(str(image_path))
                except Exception:
                    log.exception(
                        "Failed to load microscope image %s for Excel export", image_path
                    )
                    continue
                location = dataframe.columns.get_loc(column)
                if isinstance(location, slice):
                    continue
                if isinstance(location, np.ndarray):
                    continue
                try:
                    column_index = int(location) + 1
                except Exception:
                    continue
                row_number = row_idx + 2
                column_letter = get_column_letter(column_index)
                cell_reference = f"{column_letter}{row_number}"
                worksheet[cell_reference].value = None
                worksheet.add_image(image, cell_reference)
                width_px, height_px, dpi_x, dpi_y = _image_metrics(image_path)
                if height_px:
                    height_in = height_px / (dpi_y or EXCEL_EMBED_DPI)
                    required_height = _excel_row_height(height_in)
                    row_dim = worksheet.row_dimensions[row_number]
                    existing = row_dim.height or 0.0
                    row_dim.height = max(existing, required_height)
                    row_dim.customHeight = True
                if width_px:
                    width_in = width_px / (dpi_x or EXCEL_EMBED_DPI)
                    required_width = _excel_column_width(width_in)
                    col_dim = worksheet.column_dimensions[column_letter]
                    existing_width = col_dim.width or 0.0
                    col_dim.width = max(existing_width, required_width)
                    col_dim.customWidth = True
                inserted = True

        if highlight_map:
            fill = PatternFill(fill_type="solid", start_color="FFF4B5", end_color="FFF4B5")
            for column, rows in highlight_map.items():
                if column not in dataframe.columns:
                    continue
                location = dataframe.columns.get_loc(column)
                if isinstance(location, slice):
                    continue
                if isinstance(location, np.ndarray):
                    continue
                try:
                    column_index = int(location) + 1
                except Exception:
                    continue
                column_letter = get_column_letter(column_index)
                for row_idx in rows:
                    if row_idx < 0 or row_idx >= len(reset_df):
                        continue
                    row_number = row_idx + 2
                    worksheet[f"{column_letter}{row_number}"].fill = fill

        if inserted:
            workbook.save(excel_path)
            _adjust_drawing_ext_dimensions(excel_path, figure_size, log)
    finally:
        workbook.close()


def _embed_assets_with_xlsxwriter(
    writer: "pd.ExcelWriter",
    dataframe: pd.DataFrame,
    plot_name_to_path: Dict[str, Path],
    plot_dir: Path,
    origin_artifacts: Dict[str, OriginArtifact],
    figure_size: Tuple[float, float],
    log: logging.Logger,
    *,
    microscope_crops: Optional[Dict[str, Path]] = None,
    microscope_columns: Sequence[str] = (),
    highlight_map: Optional[Dict[str, Set[int]]] = None,
) -> None:
    try:
        workbook_obj = getattr(writer, "book")
    except Exception:
        return
    if workbook_obj is None:
        return
    workbook = cast(Any, workbook_obj)
    try:
        sheets_mapping = getattr(writer, "sheets")
    except Exception:
        return
    if not sheets_mapping:
        return
    worksheets = [cast(Any, sheet) for sheet in sheets_mapping.values()]
    if not worksheets:
        return
    worksheet = cast(Any, worksheets[0])

    figure_columns = [column for column in FIGURE_COLUMNS if column in dataframe.columns]
    origin_columns = [
        column
    for column in ("Figure — 1000 mA (Origin)", "Figure — low mA (Origin)")
        if column in dataframe.columns
    ]
    microscope_columns = [
        column for column in microscope_columns if column in dataframe.columns
    ]
    if not figure_columns and not origin_columns and not microscope_columns:
        return

    reset_df = dataframe.reset_index(drop=True)
    available: Dict[str, Path] = {}
    for name, path in plot_name_to_path.items():
        if path.exists():
            available[name] = path
    if plot_dir and plot_dir.exists():
        for candidate in plot_dir.glob("*.png"):
            available.setdefault(candidate.name, candidate)

    from PIL import Image as PILImage  # local import to avoid hard dependency elsewhere

    target_width_in, target_height_in = figure_size
    target_width_px, target_height_px = _excel_pixel_limits(figure_size)
    row_heights_pts: Dict[int, float] = {}
    column_widths_chars: Dict[int, float] = {}
    row_heights_px: Dict[int, int] = {}
    column_widths_px: Dict[int, int] = {}
    set_row_pixels = getattr(worksheet, "set_row_pixels", None)
    set_column_pixels = getattr(worksheet, "set_column_pixels", None)

    def ensure_row_height(row_idx: int, minimum_px: int, minimum_pts: float) -> None:
        if minimum_px < 0:
            minimum_px = 0
        if minimum_pts < 0:
            minimum_pts = 0.0

        if callable(set_row_pixels):
            try:
                set_row_pixels(row_idx, minimum_px)
            except Exception:
                pass
            else:
                row_heights_px[row_idx] = minimum_px
                row_heights_pts[row_idx] = minimum_pts
        try:
            worksheet.set_row(row_idx, minimum_pts)
        except Exception:
            return
        row_heights_pts[row_idx] = minimum_pts

    def ensure_column_width(col_idx: int, minimum_px: int, minimum_chars: float) -> None:
        if minimum_px < 0:
            minimum_px = 0
        if minimum_chars < 0:
            minimum_chars = 0.0

        if callable(set_column_pixels):
            try:
                set_column_pixels(col_idx, col_idx, minimum_px)
            except Exception:
                pass
            else:
                column_widths_px[col_idx] = minimum_px
                column_widths_chars[col_idx] = minimum_chars
                return

        try:
            worksheet.set_column(col_idx, col_idx, minimum_chars)
        except Exception:
            return
        column_widths_chars[col_idx] = minimum_chars

    def _column_index(column: str) -> Optional[int]:
        location = dataframe.columns.get_loc(column)
        if isinstance(location, slice):
            return None
        if isinstance(location, np.ndarray):
            return None
        if isinstance(location, tuple):
            return None
        if isinstance(location, list):
            return None
        try:
            return int(location)
        except Exception:
            return None

    for row_idx, row in reset_df.iterrows():
        row_number = row_idx + 1
        for column in figure_columns:
            value = row.get(column)
            if not isinstance(value, str):
                continue
            name = value.strip()
            if not name:
                continue
            image_path = available.get(name)
            if image_path is None and plot_dir:
                candidate = plot_dir / name
                if candidate.exists():
                    available[name] = candidate
                    image_path = candidate
            if image_path is None or not image_path.exists():
                continue
            width_px, height_px, dpi_x, dpi_y = _image_metrics(image_path)
            x_scale = (
                (target_width_px / float(width_px))
                if width_px
                else 1.0
            )
            y_scale = (
                (target_height_px / float(height_px))
                if height_px
                else 1.0
            )
            options: Dict[str, float] = {}
            if width_px and not math.isclose(x_scale, 1.0, rel_tol=1e-3):
                options["x_scale"] = x_scale
            if height_px and not math.isclose(y_scale, 1.0, rel_tol=1e-3):
                options["y_scale"] = y_scale
            column_index = _column_index(column)
            if column_index is None:
                continue
            worksheet.write_blank(row_number, column_index, None)
            try:
                worksheet.insert_image(row_number, column_index, str(image_path), options)
            except Exception:
                log.exception("Failed to insert plot image %s", image_path)
                continue
            ensure_row_height(
                row_number,
                int(round(target_height_in * EXCEL_EMBED_DPI)),
                _excel_row_height(target_height_in),
            )

            ensure_column_width(
                column_index,
                int(round(target_width_in * EXCEL_EMBED_DPI)),
                _excel_column_width(target_width_in),
            )

        for column in microscope_columns:
            value = row.get(column)
            if not isinstance(value, str):
                continue
            key = value.strip()
            if not key:
                continue
            image_path = None
            if microscope_crops:
                image_path = microscope_crops.get(key)
            if image_path is None:
                candidate = Path(key)
                if candidate.exists():
                    image_path = candidate
            if image_path is None or not image_path.exists():
                continue
            width_px, height_px, dpi_x, dpi_y = _image_metrics(image_path)
            if not width_px:
                width_px = 180
            if not height_px:
                height_px = 140
            width_in = width_px / (dpi_x or EXCEL_EMBED_DPI)
            height_in = height_px / (dpi_y or EXCEL_EMBED_DPI)
            column_index = _column_index(column)
            if column_index is None:
                continue
            worksheet.write_blank(row_number, column_index, None)
            try:
                worksheet.insert_image(row_number, column_index, str(image_path))
            except Exception:
                log.exception("Failed to insert microscope image %s", image_path)
                continue
            ensure_row_height(
                row_number,
                int(round(height_in * EXCEL_EMBED_DPI)),
                _excel_row_height(height_in),
            )
            ensure_column_width(
                column_index,
                int(round(width_in * EXCEL_EMBED_DPI)),
                _excel_column_width(width_in),
            )

        for column in origin_columns:
            value = row.get(column)
            if not isinstance(value, str):
                continue
            descriptor = value.strip()
            if not descriptor:
                continue
            artifact = origin_artifacts.get(descriptor)
            if artifact is None or artifact.object_path is None:
                continue
            try:
                if not artifact.object_path.exists():
                    continue
            except OSError:
                continue
            column_index = _column_index(column)
            if column_index is None:
                continue
            worksheet.write_blank(row_number, column_index, None)
            options: Dict[str, object] = {"object_position": 1}
            try:
                worksheet.insert_object(row_number, column_index, str(artifact.object_path), options)
            except Exception:
                log.exception("Failed to insert Origin object %s", artifact.object_path)
                continue
            ensure_row_height(
                row_number,
                int(target_height_px),
                _excel_row_height(target_height_in),
            )
            ensure_column_width(
                column_index,
                int(target_width_px),
                _excel_column_width(target_width_in),
            )

        if highlight_map:
            highlight_format = workbook.add_format({"bg_color": "#FFF4B5"})
            for column, rows in highlight_map.items():
                if column not in dataframe.columns:
                    continue
                column_index = _column_index(column)
                if column_index is None:
                    continue
                for row_idx in rows:
                    if row_idx < 0 or row_idx >= len(reset_df):
                        continue
                    value = reset_df.iloc[row_idx, column_index]
                    excel_row = row_idx + 1
                    if pd.isna(value):
                        worksheet.write_blank(excel_row, column_index, None, highlight_format)
                    elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                        worksheet.write_number(excel_row, column_index, float(value), highlight_format)
                    else:
                        worksheet.write(excel_row, column_index, value, highlight_format)

    excel_path = getattr(writer, "path", None)
    if isinstance(excel_path, str):
        try:
            _adjust_drawing_ext_dimensions(Path(excel_path), figure_size, log)
        except Exception:
            log.exception("Failed to adjust drawing metadata for %s", excel_path)

def _origin_object_name(obj: object) -> Optional[str]:
    if obj is None:
        return None
    for attr in ("lt_name", "name", "lname", "long_name"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalise_output_name(name: str) -> str:
    cleaned = "".join("_" if ch in _INVALID_FILENAME_CHARS else ch for ch in name.strip())
    cleaned = cleaned.strip(".")
    if not cleaned:
        return DEFAULT_OUTPUT_NAME
    return cleaned


def _safe_plot_stem(stem: str) -> str:
    normalised = unicodedata.normalize("NFKC", stem)
    cleaned_chars: list[str] = []
    invalid = _INVALID_FILENAME_CHARS | {os.sep}
    if os.altsep:
        invalid.add(os.altsep)
    for ch in normalised:
        if ch in invalid or ord(ch) < 32:
            cleaned_chars.append("_")
        else:
            cleaned_chars.append(ch)
    cleaned = "".join(cleaned_chars).strip("._ ")
    if not cleaned:
        return "measurement"
    return cleaned


def build_database(
    config: BuilderConfig,
    logger: Optional[logging.Logger] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    analysis_progress_callback: Optional[Callable[[int, int], None]] = None,
    analysis_total: Optional[int] = None,
    root_for_relpaths: Optional[Path] = None,
    skip_exports: bool = False,
    *,
    fabrication_index: FabricationIndex | None = None,
    measurement_records: Optional[Iterable[MeasurementRecord]] = None,
    microscope_index: Optional[
        Dict[Tuple[str, int, int], MicroscopeMeasurements]
    ] = None,
    video_index: Optional[
        Dict[Tuple[str, int, Optional[int]], VideoMetricsSummary]
    ] = None,
    strain_records: Optional[Dict[Tuple[str, int, int], StrainRecord]] = None,
    phase_points: Optional[Dict[str, Dict[str, float]]] = None,
) -> BuildResult:
    log = _logger(logger)
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = _normalise_output_name(getattr(config, "output_name", DEFAULT_OUTPUT_NAME))

    include_crops = bool(getattr(config, "include_microscope_crops", True))
    highlight_ocr = bool(getattr(config, "highlight_ocr_values", True))
    output_columns = list(OUTPUT_COLUMNS)
    d_column = DIAMETER_COLUMN
    D_column = GLASS_DIAMETER_COLUMN
    ratio_column = DIAMETER_RATIO_COLUMN
    microscope_image_columns: Tuple[str, ...] = ()
    if include_crops:
        microscope_image_columns = MICROSCOPE_IMAGE_COLUMNS
    if "d (µm)" in output_columns and MICROSCOPE_IMAGE_COLUMNS[0] not in output_columns:
        d_index = output_columns.index("d (µm)")
        output_columns.insert(d_index + 1, MICROSCOPE_IMAGE_COLUMNS[0])
    if "D (µm)" in output_columns and MICROSCOPE_IMAGE_COLUMNS[1] not in output_columns:
        D_index = output_columns.index("D (µm)")
        output_columns.insert(D_index + 1, MICROSCOPE_IMAGE_COLUMNS[1])

    raw_backends = tuple(config.plot_backends) if config.plot_backends else ()
    cleaned_backends = []
    for backend in raw_backends:
        if isinstance(backend, str):
            cleaned_backends.append(backend.lower())
    if config.make_plots and not cleaned_backends:
        cleaned_backends.append("matplotlib")
    deduped_backends = tuple(dict.fromkeys(cleaned_backends))
    recognised_backends = {"matplotlib", "origin"}
    for backend in deduped_backends:
        if backend not in recognised_backends:
            log.warning("Unsupported plot backend '%s'; skipping", backend)
    plot_backends = tuple(b for b in deduped_backends if b in recognised_backends)

    wants_matplotlib = "matplotlib" in plot_backends
    wants_origin_requested = "origin" in plot_backends

    fabrication_index = fabrication_index or build_fabrication_index(
        config.fabrication_files, log
    )
    stats = BuildStats()
    grouped: Dict[Tuple[str, int, int], List[MeasurementRecord]] = {}
    plot_records: List[str] = []
    plot_cache: Dict[str, Path] = {}
    plot_name_to_path: Dict[str, Path] = {}
    origin_artifacts: Dict[str, OriginArtifact] = {}
    origin_cache: Dict[str, OriginArtifact] = {}
    figure_size = _normalise_figsize(
        getattr(config, "matplotlib_figsize", DEFAULT_FIGSIZE)
    )
    plot_dir = output_dir / config.plot_dir_name
    origin_dir = output_dir / config.origin_dir_name
    origin_enabled = wants_origin_requested
    origin_disabled_reason: Optional[str] = None
    manual_microscope_files: list[Path] = []
    auto_microscope_files: list[Path] = []
    microscope_sources: list[Path] = []
    if microscope_index is None:
        manual_microscope_files = [Path(p) for p in config.microscope_files]
        auto_microscope_files = _auto_discover_microscope_paths(
            config.annealing_files, log
        )
        microscope_sources = list(
            dict.fromkeys(manual_microscope_files + auto_microscope_files)
        )
    else:
        microscope_index = dict(microscope_index)
    video_sources: list[Path] = []
    if video_index is None:
        video_sources = list(dict.fromkeys(Path(p) for p in config.video_files))
    else:
        video_index = dict(video_index)
    if strain_records is None:
        strain_records = _load_strain_records(getattr(config, "strain_files", []), log)
    else:
        strain_records = dict(strain_records)

    phase_points_map: Dict[str, Dict[str, float]] = {}
    if phase_points:
        for key, payload in phase_points.items():
            if not isinstance(key, str) or not isinstance(payload, dict):
                continue
            cleaned = {label: float(value) for label, value in payload.items() if isinstance(value, (int, float))}
            if cleaned:
                phase_points_map[key] = cleaned
    phase_points_map = dict(phase_points_map)

    analysis_total_local = analysis_total
    if analysis_total_local is None:
        analysis_total_local = len(microscope_sources) + len(video_sources)
    analysis_done = 0

    def _analysis_notify() -> None:
        if analysis_progress_callback is not None and analysis_total_local > 0:
            total_units = analysis_total_local
            current_units = min(analysis_done, total_units)
            analysis_progress_callback(current_units, total_units)

    last_micro = 0

    def _micro_progress(processed: int, _stage_total: int) -> None:
        nonlocal analysis_done, last_micro
        delta = max(processed - last_micro, 0)
        if delta:
            analysis_done += delta
            _analysis_notify()
        last_micro = processed

    last_video = 0

    def _video_progress(processed: int, _stage_total: int) -> None:
        nonlocal analysis_done, last_video
        delta = max(processed - last_video, 0)
        if delta:
            analysis_done += delta
            _analysis_notify()
        last_video = processed

    micro_callback = _micro_progress if analysis_total_local else None
    video_callback = _video_progress if analysis_total_local else None

    if analysis_total_local:
        _analysis_notify()

    if microscope_index is None:
        microscope_index, _ = _group_microscope_measurements(
            microscope_sources,
            log,
            progress_callback=micro_callback,
        )
    if video_index is None:
        video_index = _collect_video_metrics(
            video_sources,
            log,
            progress_callback=video_callback,
        )
    microscope_crop_dir = output_dir / "microscope_crops"
    microscope_crop_map: Dict[str, Path] = {}
    ocr_highlights: Dict[str, Set[int]] = {}
    if analysis_total_local and analysis_done < analysis_total_local:
        analysis_done = analysis_total_local
        _analysis_notify()
    if measurement_records is not None:
        records_list = [record for record in measurement_records]
        total = len(records_list)
        for idx, record in enumerate(records_list, start=1):
            metadata = record.metadata
            if not record.sanity_ok:
                stats.resistance_checks_failed += 1
            if metadata.draw_x is None or metadata.piece_y is None:
                log.warning(
                    "Skipping %s because the microwire draw/piece identifiers could not be parsed",
                    record.path,
                )
                stats.skipped += 1
            else:
                key = (metadata.composition_token, metadata.draw_x, metadata.piece_y)
                grouped.setdefault(key, []).append(record)
                stats.parsed += 1
            if progress_callback:
                try:
                    progress_callback(idx, total)
                except Exception:
                    pass
    else:
        total = len(config.annealing_files)
        for idx, path in enumerate(config.annealing_files, start=1):
            metadata = _metadata_from_path(path, root_for_relpaths)
            try:
                df = _load_annealing(path, expected_setpoint_mA=metadata.setpoint_mA)
            except Exception:
                log.exception("Failed to parse %s", path)
                stats.skipped += 1
                if progress_callback:
                    progress_callback(idx, total)
                continue
            ok, mean_error = _resistance_sanity_check(df)
            if not ok:
                stats.resistance_checks_failed += 1
                if mean_error is None:
                    log.warning("Râ‰ˆV/I sanity check failed for %s", path)
                else:
                    log.warning(
                        "Râ‰ˆV/I sanity check failed for %s (mean error %.2f%%)",
                        path,
                        mean_error * 100,
                    )
            if metadata.draw_x is None or metadata.piece_y is None:
                log.warning(
                    "Skipping %s because the microwire draw/piece identifiers could not be parsed",
                    path,
                )
                stats.skipped += 1
                if progress_callback:
                    progress_callback(idx, total)
                continue
            key = (metadata.composition_token, metadata.draw_x, metadata.piece_y)
            record = MeasurementRecord(
                path=path,
                metadata=metadata,
                dataframe=df,
                sanity_ok=ok,
                sanity_error=mean_error,
            )
            grouped.setdefault(key, []).append(record)
            stats.parsed += 1
            if progress_callback:
                progress_callback(idx, total)
    rows: List[Dict[str, object]] = []
    for (composition, draw_x, piece_y), records in sorted(grouped.items()):
        draw_info = fabrication_index.get_draw(composition, draw_x)
        piece_info = fabrication_index.get_piece(composition, draw_x, piece_y)
        row: Dict[str, object] = {column: None for column in output_columns}
        row["Composition"] = composition
        row["Microwire"] = _microwire_label(draw_x, piece_y)
        row["d (µm)"] = None
        row["D (µm)"] = None
        row[ratio_column] = None
        row["Length (m)"] = _value_for_output(piece_info, "length_m")
        row["Production datetime"] = _value_for_output(draw_info, "production_datetime")
        row["Mass (g)"] = _value_for_output(draw_info, "mass_g")
        row["Resistance (Ω)"] = _value_for_output(draw_info, "fabrication_resistance_ohm")
        row["Temperature (°C)"] = _value_for_output(draw_info, "fabrication_temperature_c")
        row["Winding speed (m/min)"] = _value_for_output(draw_info, "winding_speed_m_per_min")
        row["Glass feeding (mm/min)"] = _value_for_output(draw_info, "glass_feed_mm_per_min")
        row["Underpressure"] = _value_for_output(draw_info, "underpressure")
        row["Notes"] = _compose_notes(draw_info, piece_info)
        phase_entry = phase_points_map.get(f"{composition}|{draw_x}|{piece_y}", {})
        if phase_entry:
            as_value = phase_entry.get("As1")
            if as_value is None:
                as_value = phase_entry.get("As")
            if as_value is not None:
                row["As (mA)"] = as_value
            ms_value = phase_entry.get("Ms1")
            if ms_value is None:
                ms_value = phase_entry.get("Ms")
            if ms_value is not None:
                row["Ms (mA)"] = ms_value
        row_highlights: Set[str] = set()
        d_detection: Optional[MicroscopeDetection] = None
        D_detection: Optional[MicroscopeDetection] = None
        d_numeric = _parse_numeric(row["d (µm)"])
        D_numeric = _parse_numeric(row["D (µm)"])
        ratio_numeric = _parse_numeric(row[ratio_column])
        microscope_data = microscope_index.get((composition, draw_x, piece_y))
        if microscope_data:
            if d_numeric is None:
                d_detection = microscope_data.best_core_detection()
                if (
                    isinstance(d_detection, MicroscopeDetection)
                    and getattr(d_detection, "category", None) == "core"
                    and isinstance(d_detection.value, (int, float))
                    and math.isfinite(float(d_detection.value))
                ):
                    row[d_column] = float(d_detection.value)
                    d_numeric = float(d_detection.value)
                    if d_detection.source == "ocr":
                        row_highlights.add(d_column)
            if D_numeric is None:
                D_detection = microscope_data.best_glass_detection()
                if (
                    isinstance(D_detection, MicroscopeDetection)
                    and getattr(D_detection, "category", None) == "glass"
                    and isinstance(D_detection.value, (int, float))
                    and math.isfinite(float(D_detection.value))
                ):
                    row[D_column] = float(D_detection.value)
                    D_numeric = float(D_detection.value)
                    if D_detection.source == "ocr":
                        row_highlights.add(D_column)
            if ratio_numeric is None and d_numeric is not None and D_numeric not in (None, 0):
                try:
                    ratio = d_numeric / D_numeric
                except ZeroDivisionError:
                    ratio = None
                if ratio is not None and math.isfinite(ratio):
                    row[ratio_column] = ratio
                    ratio_numeric = ratio
        if include_crops:
            if MICROSCOPE_IMAGE_COLUMNS[0] in row:
                row[MICROSCOPE_IMAGE_COLUMNS[0]] = row.get(MICROSCOPE_IMAGE_COLUMNS[0])
            if MICROSCOPE_IMAGE_COLUMNS[1] in row:
                row[MICROSCOPE_IMAGE_COLUMNS[1]] = row.get(MICROSCOPE_IMAGE_COLUMNS[1])
            if d_detection and d_detection.image_path is not None:
                crop_path = d_detection.ensure_crop(
                    microscope_crop_dir,
                    f"{composition}_{draw_x}_{piece_y}_d",
                )
                if crop_path is not None:
                    try:
                        rel_path = crop_path.relative_to(output_dir)
                        rel_text = str(rel_path).replace(os.sep, "/")
                    except ValueError:
                        rel_text = str(crop_path)
                    row[MICROSCOPE_IMAGE_COLUMNS[0]] = rel_text
                    microscope_crop_map[rel_text] = crop_path
            if D_detection and D_detection.image_path is not None:
                crop_path = D_detection.ensure_crop(
                    microscope_crop_dir,
                    f"{composition}_{draw_x}_{piece_y}_D",
                )
                if crop_path is not None:
                    try:
                        rel_path = crop_path.relative_to(output_dir)
                        rel_text = str(rel_path).replace(os.sep, "/")
                    except ValueError:
                        rel_text = str(crop_path)
                    row[MICROSCOPE_IMAGE_COLUMNS[1]] = rel_text
                    microscope_crop_map[rel_text] = crop_path
        video_data = video_index.get((composition, draw_x, piece_y))
        if video_data is None:
            video_data = video_index.get((composition, draw_x, None))
        if video_data:
            temp_numeric = _parse_numeric(row["Temperature (°C)"])
            if temp_numeric is None:
                temp = video_data.temperature()
                if temp is not None:
                    row["Temperature (°C)"] = temp
                    row_highlights.add("Temperature (°C)")
            under_numeric = _parse_numeric(row["Underpressure"])
            if under_numeric is None:
                under_value = video_data.underpressure()
                if under_value is not None:
                    row["Underpressure"] = under_value
                    row_highlights.add("Underpressure")
            wind_numeric = _parse_numeric(row["Winding speed (m/min)"])
            if wind_numeric is None:
                wind = video_data.winding_speed()
                if wind is not None:
                    row["Winding speed (m/min)"] = wind
                    row_highlights.add("Winding speed (m/min)")
            glass_numeric = _parse_numeric(row["Glass feeding (mm/min)"])
            if glass_numeric is None:
                glass = video_data.glass_feed()
                if glass is not None:
                    row["Glass feeding (mm/min)"] = glass
                    row_highlights.add("Glass feeding (mm/min)")
        strain_record = strain_records.get((composition, draw_x, piece_y))
        if strain_record:
            strain_value = _format_strain_value(strain_record)
            if strain_value is not None:
                row[STRAIN_COLUMN] = strain_value
        ratio_display = _parse_numeric(row["d/D"])
        if ratio_display is not None:
            row["d/D"] = round(ratio_display, 3)
        if not draw_info:
            stats.missing_draw += 1
        if not piece_info:
            stats.missing_piece += 1
        high_record = _select_high_measurement(records)
        low_record = _select_low_measurement(records)
        if low_record and high_record:
            high_sp = high_record.metadata.setpoint_mA
            low_sp = low_record.metadata.setpoint_mA
            setpoints = {r.metadata.setpoint_mA for r in records if r.metadata.setpoint_mA is not None}
            if high_sp is not None and low_sp == high_sp and len(setpoints) <= 1:
                low_record = None
        if high_record:
            row["File 1000 mA"] = high_record.metadata.file_name
        else:
            stats.missing_high_measurement += 1
            log.warning("No 1000 mA measurement found for %s %s", composition, row["Microwire"] or "(unknown)")
        if low_record:
            row["File low mA"] = low_record.metadata.file_name
            if low_record.metadata.setpoint_mA is not None:
                row["Low mA value (mA)"] = low_record.metadata.setpoint_mA
        else:
            stats.missing_low_measurement += 1
            log.warning("No low-current measurement found for %s %s", composition, row["Microwire"] or "(unknown)")
        if wants_matplotlib:
            if high_record:
                cached = plot_cache.get(high_record.metadata.measurement_id)
                if cached is None:
                    try:
                        cached = _plot_measurement_matplotlib(
                            high_record.dataframe,
                            high_record.path,
                            plot_dir,
                            figure_size,
                        )
                        plot_cache[high_record.metadata.measurement_id] = cached
                    except Exception:
                        log.exception("Failed to generate plot for %s", high_record.path)
                        cached = None
                if cached is not None:
                    figure_name = Path(cached).name
                    row["Figure — 1000 mA"] = figure_name
                    plot_name_to_path.setdefault(figure_name, cached)
                    if figure_name not in plot_records:
                        plot_records.append(figure_name)
            if low_record:
                cached = plot_cache.get(low_record.metadata.measurement_id)
                if cached is None:
                    try:
                        cached = _plot_measurement_matplotlib(
                            low_record.dataframe,
                            low_record.path,
                            plot_dir,
                            figure_size,
                        )
                        plot_cache[low_record.metadata.measurement_id] = cached
                    except Exception:
                        log.exception("Failed to generate plot for %s", low_record.path)
                        cached = None
                if cached is not None:
                    figure_name = Path(cached).name
                    row["Figure — low mA"] = figure_name
                    plot_name_to_path.setdefault(figure_name, cached)
                    if figure_name not in plot_records:
                        plot_records.append(figure_name)
        if origin_enabled:
            if high_record:
                cached_origin = origin_cache.get(high_record.metadata.measurement_id)
                if cached_origin is None:
                    try:
                        cached_origin = _plot_measurement_origin(
                            high_record.dataframe,
                            high_record.path,
                            origin_dir,
                            log,
                        )
                    except RuntimeError as exc:
                        if origin_disabled_reason is None:
                            origin_disabled_reason = str(exc) or exc.__class__.__name__
                            log.warning("Origin plotting disabled: %s", origin_disabled_reason)
                        origin_enabled = False
                        cached_origin = None
                    except Exception:
                        log.exception("Failed to generate Origin plot for %s", high_record.path)
                        cached_origin = None
                    else:
                        if cached_origin is not None:
                            origin_cache[high_record.metadata.measurement_id] = cached_origin
                            origin_artifacts.setdefault(cached_origin.descriptor, cached_origin)
                if cached_origin is not None:
                    row["Figure — 1000 mA (Origin)"] = cached_origin.descriptor
            if origin_enabled and low_record:
                cached_origin = origin_cache.get(low_record.metadata.measurement_id)
                if cached_origin is None:
                    try:
                        cached_origin = _plot_measurement_origin(
                            low_record.dataframe,
                            low_record.path,
                            origin_dir,
                            log,
                        )
                    except RuntimeError as exc:
                        if origin_disabled_reason is None:
                            origin_disabled_reason = str(exc) or exc.__class__.__name__
                            log.warning("Origin plotting disabled: %s", origin_disabled_reason)
                        origin_enabled = False
                        cached_origin = None
                    except Exception:
                        log.exception("Failed to generate Origin plot for %s", low_record.path)
                        cached_origin = None
                    else:
                        if cached_origin is not None:
                            origin_cache[low_record.metadata.measurement_id] = cached_origin
                            origin_artifacts.setdefault(cached_origin.descriptor, cached_origin)
                if cached_origin is not None:
                    row["Figure — low mA (Origin)"] = cached_origin.descriptor
        row_index = len(rows)
        rows.append(row)
        if row_highlights:
            for column in row_highlights:
                if column in output_columns:
                    ocr_highlights.setdefault(column, set()).add(row_index)
        stats.rows_built += 1
    if rows:
        df_out = pd.DataFrame(rows, columns=output_columns)
    else:
        df_out = pd.DataFrame(columns=output_columns)
    exports: Dict[str, Path] = {}
    if skip_exports:
        requested_formats: Tuple[str, ...] = ()
    else:
        requested_formats = (
            tuple(dict.fromkeys(config.export_formats))
            if config.export_formats
            else ("csv",)
        )
    behaviours = {
        (key.lower() if isinstance(key, str) else ""): str(value).lower()
        for key, value in (config.export_behaviour or {}).items()
    }
    for fmt in requested_formats:
        fmt_lower = fmt.lower()
        if fmt_lower == "csv":
            csv_path = output_dir / f"{output_name}.csv"
            behaviour = behaviours.get("csv", "replace")
            if behaviour == "update":
                if csv_path.exists():
                    _update_existing_csv_with_strain(csv_path, strain_records, output_columns, log)
                    exports["csv"] = csv_path
                    continue
                log.warning("CSV export %s does not exist; creating a new file instead", csv_path)
                behaviour = "replace"
            to_write = df_out
            if behaviour == "append" and csv_path.exists():
                try:
                    existing = pd.read_csv(csv_path)
                except Exception:
                    log.exception("Failed to read existing CSV at %s; overwriting", csv_path)
                else:
                    to_write = pd.concat([existing, df_out], ignore_index=True)
                    to_write = to_write.drop_duplicates()
            to_write.to_csv(csv_path, index=False)
            exports["csv"] = csv_path
        elif fmt_lower == "excel":
            excel_path = output_dir / f"{output_name}.xlsx"
            behaviour = behaviours.get("excel", "replace")
            if behaviour == "update":
                if excel_path.exists():
                    try:
                        _update_existing_excel_with_strain(excel_path, strain_records, log)
                    except RuntimeError:
                        log.exception("Unable to update Excel file at %s", excel_path)
                        raise
                    exports["excel"] = excel_path
                    continue
                log.warning("Excel export %s does not exist; creating a new file instead", excel_path)
                behaviour = "replace"
            to_write = df_out
            if behaviour == "append" and excel_path.exists():
                try:
                    existing = pd.read_excel(excel_path)
                except Exception:
                    log.exception("Failed to read existing Excel at %s; overwriting", excel_path)
                else:
                    to_write = pd.concat([existing, df_out], ignore_index=True)
                    to_write = to_write.drop_duplicates()
            excel_frame = to_write.copy()
            columns_to_blank = [
                column for column in (FIGURE_COLUMNS + ORIGIN_FIGURE_COLUMNS) if column in excel_frame.columns
            ]
            if microscope_image_columns:
                columns_to_blank.extend(
                    column for column in microscope_image_columns if column in excel_frame.columns
                )
            for column in columns_to_blank:
                excel_frame[column] = None
            try:
                import xlsxwriter  # noqa: F401

                with pd.ExcelWriter(excel_path, engine="xlsxwriter") as writer:
                    excel_frame.to_excel(writer, index=False, sheet_name="Sheet1")
                    try:
                        _embed_assets_with_xlsxwriter(
                            writer,
                            to_write,
                            plot_name_to_path,
                            plot_dir,
                            origin_artifacts,
                            figure_size,
                            log,
                            microscope_crops=microscope_crop_map if include_crops else None,
                            microscope_columns=microscope_image_columns,
                            highlight_map=ocr_highlights if highlight_ocr else None,
                        )
                    except Exception:
                        log.exception("Failed to embed figures into %s", excel_path)
                try:
                    _adjust_drawing_ext_dimensions(excel_path, figure_size, log)
                except Exception:
                    log.exception("Failed to adjust drawing metadata for %s", excel_path)
            except ImportError:
                excel_frame.to_excel(excel_path, index=False)
                try:
                    _embed_plots_in_excel_openpyxl(
                        excel_path,
                        to_write,
                        plot_name_to_path,
                        plot_dir,
                        log,
                        figure_size,
                        microscope_columns=microscope_image_columns,
                        microscope_crops=microscope_crop_map if include_crops else None,
                        highlight_map=ocr_highlights if highlight_ocr else None,
                    )
                    try:
                        _adjust_drawing_ext_dimensions(excel_path, figure_size, log)
                    except Exception:
                        log.exception("Failed to adjust drawing metadata for %s", excel_path)
                except Exception:
                    log.exception("Failed to embed figure images into %s", excel_path)
            exports["excel"] = excel_path
        else:
            log.warning("Unsupported export format '%s'; skipping", fmt)
    log.info(
        "Measurements parsed: %s | Skipped: %s | Rows built: %s | Missing draw info: %s | Missing piece info: %s | Missing 1000 mA: %s | Missing low mA: %s | Râ‰ˆV/I failures: %s",
        stats.parsed,
        stats.skipped,
        stats.rows_built,
        stats.missing_draw,
        stats.missing_piece,
        stats.missing_high_measurement,
        stats.missing_low_measurement,
        stats.resistance_checks_failed,
    )

    if wants_matplotlib:
        try:
            shutil.rmtree(plot_dir, ignore_errors=True)
        except Exception:
            pass

    return BuildResult(
        dataframe=df_out,
        exports=exports,
        plot_paths=plot_records,
        origin_artifacts=origin_artifacts,
        stats=stats,
        microscope_crops=microscope_crop_map if include_crops else {},
        ocr_highlights=ocr_highlights if highlight_ocr else {},
    )


__all__ = [
    "BuilderConfig",
    "BuildResult",
    "BuildStats",
    "OriginArtifact",
    "FabricationIndex",
    "build_database",
    "build_fabrication_index",
    "LOGGER_NAME",
    "DEFAULT_OUTPUT_NAME",
]


