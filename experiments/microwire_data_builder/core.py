"""Core data processing for the microwire database builder."""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import logging
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

LOGGER_NAME = "microwire_data_builder"
ASSUMED_COLS = "I_A,V_V,R_ohm"
PLOT_STYLE_DESCRIPTION = "red=up, blue=down"
R_CHECK_THRESHOLD = 0.05
CSV_NAME = "microwire_database.csv"
EXCEL_NAME = "microwire_database.xlsx"
LOG_NAME = "microwire_database.log"
PLOT_DIR_NAME = "plots"

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
    "zloženie": "composition_label",
    "zlozenie": "composition_label",
    "composition": "composition_label",
    "dátum a čas výroby": "production_datetime",
    "datum a cas vyroby": "production_datetime",
    "datumacasvyroby": "production_datetime",
    "hmotnosť": "mass_g",
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
    "p.č": "piece_y",
    "p.c": "piece_y",
    "p.č.": "piece_y",
    "počet otáčok": "piece_turns",
    "pocet otacok": "piece_turns",
    "dĺžka (m)": "length_m",
    "dlzka (m)": "length_m",
    "d (µm)": "d_um",
    "d (um)": "d_um",
    "d (μm)": "d_um",
    "d(µm)": "d_um",
    "d(um)": "d_um",
    "d(μm)": "d_um",
    "D (µm)": "D_um",
    "D (um)": "D_um",
    "D (μm)": "D_um",
    "D(µm)": "D_um",
    "D(um)": "D_um",
    "D(μm)": "D_um",
    "d/D": "d_over_D",
    "d/d": "d_over_D",
    "Datum": "piece_date",
    "Dátum": "piece_date",
    "datum": "piece_date",
    "dátum": "piece_date",
}

ANNEALING_COLUMNS = ["I_A", "V_V", "R_ohm"]
PERCENTILES = [0, 25, 50, 75, 100]

DRAW_PATTERN = re.compile(r"^(?P<draw>\d+)")
PIECE_PATTERN = re.compile(r"^(?P<piece>\d+)")
XY_PATTERN = re.compile(r"(\d+)_+(\d+)")
SETPOINT_PATTERN = re.compile(r"(\d{1,4})mA", re.IGNORECASE)
ALT_VARIANT_PATTERN = re.compile(r"(?:s\d+|\d+_\d+)a(?!\w)", re.IGNORECASE)


@dataclass
class BuilderConfig:
    """Configuration for the database builder."""

    fabrication_files: List[Path]
    annealing_files: List[Path]
    output_dir: Path
    make_plots: bool = False
    export_excel: bool = False
    plot_dir_name: str = PLOT_DIR_NAME
    log_file_name: str = LOG_NAME


@dataclass
class BuildStats:
    """Accumulates processing statistics."""

    parsed: int = 0
    skipped: int = 0
    missing_draw: int = 0
    missing_piece: int = 0
    resistance_checks_failed: int = 0


@dataclass
class BuildResult:
    """Return value from :func:`build_database`."""

    dataframe: pd.DataFrame
    csv_path: Path
    excel_path: Optional[Path]
    plot_paths: List[Path]
    stats: BuildStats


class FabricationIndex:
    """Lookup tables populated from fabrication spreadsheets."""

    def __init__(self) -> None:
        self.draw_level: Dict[Tuple[str, int], Dict[str, object]] = {}
        self.piece_level: Dict[Tuple[str, int, int], Dict[str, object]] = {}

    def set_draw(self, composition: str, draw_x: int, data: Dict[str, object]) -> None:
        key = (composition, draw_x)
        existing = self.draw_level.get(key, {})
        existing.update(data)
        self.draw_level[key] = existing

    def set_piece(self, composition: str, draw_x: int, piece_y: int, data: Dict[str, object]) -> None:
        key = (composition, draw_x, piece_y)
        existing = self.piece_level.get(key, {})
        existing.update(data)
        self.piece_level[key] = existing

    def get_draw(self, composition: str, draw_x: Optional[int]) -> Dict[str, object]:
        if draw_x is None:
            return {}
        return self.draw_level.get((composition, draw_x), {})

    def get_piece(self, composition: str, draw_x: Optional[int], piece_y: Optional[int]) -> Dict[str, object]:
        if draw_x is None or piece_y is None:
            return {}
        return self.piece_level.get((composition, draw_x, piece_y), {})


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
    return text


def _header_key(value: object) -> Optional[str]:
    text = _normalise_text(value)
    if not text:
        return None
    hint = HEADER_HINTS.get(text)
    if hint:
        return hint
    ascii_text = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in ascii_text if not unicodedata.combining(ch))
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
    if "p.c" in lowered or "p.č" in lowered or simple_compact == "pc":
        return "piece_y"
    if "pocet" in lowered and "otac" in lowered:
        return "piece_turns"
    if "dlzk" in lowered or "dlžk" in lowered:
        return "length_m"
    if lowered.strip().startswith("d") and "µm" in lowered:
        first = ascii_text.strip()[:1]
        if first == "D":
            return "D_um"
        return "d_um"
    if lowered.strip().startswith("d") and "um" in lowered and "µ" not in lowered:
        first = ascii_text.strip()[:1]
        if first == "D":
            return "D_um"
        return "d_um"
    if "d/d" in lowered or simple_compact == "dd":
        return "d_over_D"
    if ("datum" in lowered or "dátum" in lowered) and "cas" not in lowered:
        return "piece_date"
    return None


def _is_nan(value: object) -> bool:
    return isinstance(value, float) and math.isnan(value)


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


def _parse_datetime(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    text = _normalise_text(value)
    if not text:
        return None
    try:
        dt = pd.to_datetime(text, dayfirst=False, errors="coerce")
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


def _composition_from_path(path: Path) -> str:
    stem = path.stem
    return stem.split()[0]


def _parse_draw_rows(
    df: pd.DataFrame,
    headers: List[Optional[str]],
    composition: str,
    index: FabricationIndex,
    logger: logging.Logger,
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
        record: Dict[str, object] = {}
        for col_idx, field in enumerate(headers):
            if col_idx == 0 or not field:
                continue
            value = row.iloc[col_idx] if col_idx < len(row) else None
            parsed, raw = _extract_field_value(field, value)
            record[field] = parsed
            if field in RAW_VALUE_FIELDS:
                record[f"{field}_raw"] = raw
        index.set_draw(composition, draw_x, record)


def _parse_piece_rows(
    df: pd.DataFrame,
    headers: List[Optional[str]],
    composition: str,
    draw_x: Optional[int],
    index: FabricationIndex,
    logger: logging.Logger,
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
        record: Dict[str, object] = {}
        for col_idx, field in enumerate(headers):
            if col_idx == 0 or not field:
                continue
            value = row.iloc[col_idx] if col_idx < len(row) else None
            parsed, raw = _extract_field_value(field, value)
            record[field] = parsed
            if field in RAW_VALUE_FIELDS:
                record[f"{field}_raw"] = raw
        index.set_piece(composition, draw_x, piece_y, record)


def _read_excel(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, header=None, dtype=object)
    except ImportError as exc:  # pragma: no cover - pandas provides helpful message
        raise
    except ValueError as exc:
        raise
    return df


def _parse_composition_workbook(path: Path, index: FabricationIndex, logger: logging.Logger) -> None:
    df = _read_excel(path)
    if df.empty:
        logger.warning("%s is empty", path)
        return
    header_row = df.iloc[0]
    headers = [_header_key(value) for value in header_row]
    data = df.iloc[1:].reset_index(drop=True)
    composition = _composition_from_path(path)
    _parse_draw_rows(data, headers, composition, index, logger)


def _parse_piece_workbook(path: Path, index: FabricationIndex, logger: logging.Logger) -> None:
    df = _read_excel(path)
    if df.empty:
        logger.warning("%s is empty", path)
        return
    header_idx = None
    for idx, row in df.iterrows():
        values = [_normalise_text(cell).lower() for cell in row.tolist()]
        if any("p.c" in v or "p.č" in v or v == "pc" for v in values if v):
            header_idx = idx
            break
    if header_idx is None:
        logger.warning("%s: unable to locate header row", path)
        return
    headers = [_header_key(value) for value in df.iloc[header_idx]]
    data = df.iloc[header_idx + 1 :].reset_index(drop=True)
    stem = path.stem
    match = re.search(r"(?P<draw>\d+)[._](?P<comp>[A-Za-z0-9]+)", stem)
    draw_x: Optional[int] = None
    composition = _composition_from_path(path)
    if match:
        draw_x = int(match.group("draw"))
        composition = match.group("comp")
    _parse_piece_rows(data, headers, composition, draw_x, index, logger)


def build_fabrication_index(
    fabrication_files: Sequence[Path],
    logger: Optional[logging.Logger] = None,
) -> FabricationIndex:
    log = _logger(logger)
    index = FabricationIndex()
    for path in fabrication_files:
        if path.suffix.lower() != ".xlsx":
            log.debug("Skipping non-Excel file %s", path)
            continue
        stem = path.stem
        parent_stem = path.parent.name
        if re.match(r"^\d+", stem) or re.match(r"^\d+", parent_stem):
            _parse_piece_workbook(path, index, log)
        else:
            _parse_composition_workbook(path, index, log)
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


def _load_annealing(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(path, sep=None, engine="python", names=ANNEALING_COLUMNS, header=None)
    except (csv.Error, pd.errors.ParserError):
        df = pd.read_csv(
            path,
            delim_whitespace=True,
            engine="python",
            names=ANNEALING_COLUMNS,
            header=None,
        )
    df = df.iloc[:, :3].copy()
    for column in ANNEALING_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["I_A", "R_ohm"])
    return df.reset_index(drop=True)


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


def _linear_fit(currents: np.ndarray, resistances: np.ndarray) -> Tuple[float, float, float, np.ndarray]:
    if currents.size < 2:
        return (float("nan"), float("nan"), float("nan"), np.full_like(resistances, np.nan))
    slope, intercept = np.polyfit(currents, resistances, 1)
    fitted = slope * currents + intercept
    ss_res = float(np.sum((resistances - fitted) ** 2))
    ss_tot = float(np.sum((resistances - np.mean(resistances)) ** 2))
    if ss_tot == 0:
        r2 = 1.0
    else:
        r2 = 1.0 - ss_res / ss_tot
    return slope, intercept, r2, fitted


def _percentile_anchors(currents: np.ndarray, resistances: np.ndarray) -> Dict[int, float]:
    if currents.size == 0:
        return {p: float("nan") for p in PERCENTILES}
    sorted_indices = np.argsort(currents)
    sorted_currents = currents[sorted_indices]
    sorted_resistances = resistances[sorted_indices]
    anchors: Dict[int, float] = {}
    for pct in PERCENTILES:
        target = np.percentile(sorted_currents, pct)
        anchors[pct] = float(np.interp(target, sorted_currents, sorted_resistances))
    return anchors


def _curve_features(df: pd.DataFrame) -> Dict[str, object]:
    currents = df["I_A"].to_numpy(dtype=float)
    voltages = df["V_V"].to_numpy(dtype=float)
    resistances = df["R_ohm"].to_numpy(dtype=float)
    order = np.argsort(currents)
    currents = currents[order]
    voltages = voltages[order]
    resistances = resistances[order]
    slope, intercept, r2, fitted = _linear_fit(currents, resistances)
    gradients = np.gradient(resistances, currents) if currents.size >= 2 else np.array([float("nan")])
    integrate = getattr(np, "trapezoid", np.trapz)
    area = float(integrate(resistances, currents)) if currents.size >= 2 else float("nan")
    r0 = float(resistances[np.argmin(np.abs(currents))]) if currents.size else float("nan")
    rmax = float(resistances[np.argmax(currents)]) if currents.size else float("nan")
    anchors = _percentile_anchors(currents, resistances)
    with np.errstate(divide="ignore", invalid="ignore"):
        nonlinearity = np.abs(resistances - fitted) / np.maximum(np.abs(resistances), 1e-9)
    return {
        "points": int(currents.size),
        "current_min_A": float(np.min(currents)) if currents.size else float("nan"),
        "current_max_A": float(np.max(currents)) if currents.size else float("nan"),
        "current_mean_A": float(np.mean(currents)) if currents.size else float("nan"),
        "resistance_min_ohm": float(np.min(resistances)) if currents.size else float("nan"),
        "resistance_max_ohm": float(np.max(resistances)) if currents.size else float("nan"),
        "resistance_mean_ohm": float(np.mean(resistances)) if currents.size else float("nan"),
        "slope_dR_dI_ohm_per_A": float(slope),
        "intercept_ohm": float(intercept),
        "linear_r2": float(r2),
        "grad_mean_dR_dI_ohm_per_A": float(np.nanmean(gradients)) if gradients.size else float("nan"),
        "area_RdI_ohmA": area,
        "R_at_I0_ohm": r0,
        "R_at_Imax_ohm": rmax,
        "nonlinearity_mae_frac": float(np.nanmean(nonlinearity)) if nonlinearity.size else float("nan"),
        "R_at_Ipct_0_ohm": anchors[0],
        "R_at_Ipct_25_ohm": anchors[25],
        "R_at_Ipct_50_ohm": anchors[50],
        "R_at_Ipct_75_ohm": anchors[75],
        "R_at_Ipct_100_ohm": anchors[100],
    }


def _plot_measurement(df: pd.DataFrame, source: Path, plot_dir: Path) -> Path:
    from plotting.current_annealing.core import plot_one
    from plotting.utils import format_annealing_title

    plot_dir.mkdir(parents=True, exist_ok=True)
    title = format_annealing_title(source.stem)
    plot_df = pd.DataFrame({"I_mA": df["I_A"] * 1e3, "R_Ohm": df["R_ohm"]})
    fig, fname = plot_one(plot_df, title)
    plot_path = plot_dir / f"{fname}.png"
    fig.savefig(plot_path, dpi=300)
    fig.close()
    return plot_path


def build_database(
    config: BuilderConfig,
    logger: Optional[logging.Logger] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    root_for_relpaths: Optional[Path] = None,
) -> BuildResult:
    log = _logger(logger)
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    fabrication_index = build_fabrication_index(config.fabrication_files, log)
    rows: List[Dict[str, object]] = []
    stats = BuildStats()
    plot_paths: List[Path] = []
    total = len(config.annealing_files)
    for idx, path in enumerate(config.annealing_files, start=1):
        try:
            df = _load_annealing(path)
        except Exception as exc:
            log.exception("Failed to parse %s", path)
            stats.skipped += 1
            if progress_callback:
                progress_callback(idx, total)
            continue
        metadata = _metadata_from_path(path, root_for_relpaths)
        row: Dict[str, object] = dataclasses.asdict(metadata)
        row["source_type"] = "annealing_txt"
        row["microwire_xy"] = (
            f"{metadata.draw_x}/{metadata.piece_y}" if metadata.draw_x is not None and metadata.piece_y is not None else ""
        )
        row["assumed_cols"] = ASSUMED_COLS
        ok, mean_error = _resistance_sanity_check(df)
        row["R_equals_V_over_I_check"] = bool(ok)
        if mean_error is not None:
            row["R_equals_V_over_I_error"] = mean_error
        else:
            row["R_equals_V_over_I_error"] = float("nan")
        if not ok:
            stats.resistance_checks_failed += 1
        features = _curve_features(df)
        row.update(features)
        draw_info = fabrication_index.get_draw(metadata.composition_token, metadata.draw_x)
        if draw_info:
            row.update(draw_info)
        else:
            stats.missing_draw += 1
        piece_info = fabrication_index.get_piece(metadata.composition_token, metadata.draw_x, metadata.piece_y)
        if piece_info:
            row.update(piece_info)
        else:
            stats.missing_piece += 1
        if config.make_plots:
            plot_dir = output_dir / config.plot_dir_name
            try:
                plot_path = _plot_measurement(df, path, plot_dir)
                row["plot_png_path"] = os.fspath(plot_path)
                row["plot_style"] = PLOT_STYLE_DESCRIPTION
                plot_paths.append(plot_path)
            except Exception:
                log.exception("Failed to generate plot for %s", path)
        rows.append(row)
        stats.parsed += 1
        if progress_callback:
            progress_callback(idx, total)
    if not rows:
        df_out = pd.DataFrame(columns=["measurement_id"])
    else:
        df_out = pd.DataFrame(rows)
    csv_path = output_dir / CSV_NAME
    df_out.to_csv(csv_path, index=False)
    excel_path = None
    if config.export_excel:
        excel_path = output_dir / EXCEL_NAME
        df_out.to_excel(excel_path, index=False)
    log.info(
        "Measurements parsed: %s | Skipped: %s | Missing draw info: %s | Missing piece info: %s | R≈V/I failures: %s",
        stats.parsed,
        stats.skipped,
        stats.missing_draw,
        stats.missing_piece,
        stats.resistance_checks_failed,
    )
    return BuildResult(
        dataframe=df_out,
        csv_path=csv_path,
        excel_path=excel_path,
        plot_paths=plot_paths,
        stats=stats,
    )


__all__ = [
    "BuilderConfig",
    "BuildResult",
    "BuildStats",
    "FabricationIndex",
    "build_database",
    "build_fabrication_index",
    "LOGGER_NAME",
]
