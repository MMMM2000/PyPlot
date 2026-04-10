from __future__ import annotations

import base64
import html
import io
import json
import math
import os
import re
import shutil
import tempfile
import unicodedata
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import ListedColormap
from pandas.plotting import parallel_coordinates, scatter_matrix
from scipy import stats

ROW_SCOPE_ALL = "all"
ROW_SCOPE_FILTERED = "filtered"
ROW_SCOPE_SELECTED = "selected"

INPUT_KIND_AUTO = "auto"
INPUT_KIND_PROJECT = "project"
INPUT_KIND_EXCEL = "excel"
INPUT_KIND_DATAFRAME = "dataframe"

STATUS_OK = "OK"
STATUS_BROKE = "Broke"
STATUS_NO_DATA = "No data"

AGGREGATION_RAW = "raw"
AGGREGATION_PER_WIRE_MEDIAN = "per_wire_median"
AGGREGATION_PER_WIRE_BEST = "per_wire_best"
SUPPORTED_AGGREGATION_MODES = (
    AGGREGATION_RAW,
    AGGREGATION_PER_WIRE_MEDIAN,
    AGGREGATION_PER_WIRE_BEST,
)

STATUS_COLORS = {
    STATUS_OK: "#2e8b57",
    STATUS_BROKE: "#d94f4f",
    STATUS_NO_DATA: "#9c9c9c",
}

DERIVED_GEOMETRY_COLUMNS = [
    "coat_thickness_um",
    "core_fraction_linear",
    "glass_area_fraction",
    "metal_area_um2",
    "glass_area_um2",
]

GEOMETRY_COLUMNS = ["d (µm)", "D (µm)", "d/D"]
FABRICATION_COLUMNS = [
    "Core temperature (°C)",
    "Glass temperature (°C)",
    "Winding speed (m/min)",
    "Glass feeding (mm/min)",
    "Underpressure",
    "Length (m)",
    "Mass (g)",
]
ANNEALING_COLUMNS = [
    "e/a",
    "As (mA)",
    "Ms (mA)",
    "As1 (mA)",
    "Af1 (mA)",
    "Ms1 (mA)",
    "Mf1 (mA)",
    "As2 (mA)",
    "Af2 (mA)",
    "Ms2 (mA)",
    "Mf2 (mA)",
    "As current density (A/mm^2)",
    "Ms current density (A/mm^2)",
    "Low mA value (mA)",
]
STRESS_TEST_CURRENT_COLUMNS = [
    "Stress/strain current (mA)",
    "Stress/strain current density (A/mm^2)",
    "Fracture stress/strain current (mA)",
    "Fracture stress/strain current density (A/mm^2)",
]
CURRENT_FEATURES = list(dict.fromkeys(ANNEALING_COLUMNS + STRESS_TEST_CURRENT_COLUMNS))
COMPOSITION_FEATURES = [
    "Mn",
    "Ni",
    "Fe",
    "Ga",
    "Co",
    "Cu",
    "Si",
    "Sn",
    "has_Mn",
    "has_Co",
    "has_Cu",
    "has_Si",
    "has_Sn",
]
FUNCTIONAL_COLUMNS = [
    "Strain (%)",
    "Fracture strain (%)",
    "Stress (MPa)",
    "Fracture stress (MPa)",
    "Strain",
    "Legacy strain",
    "Legacy stress (MPa)",
]
LEGACY_OUTCOME_COLUMNS = ["Brittle", "Broke", "is_broken"]
DERIVED_OUTCOME_COLUMNS = ["strain_abs", "fracture_strain_abs"]
ENDPOINT_COLUMNS = [
    "strain_abs",
    "fracture_strain_abs",
    "Stress (MPa)",
    "Fracture stress (MPa)",
]
REPORT_EXTRA_COLUMNS = [
    "Tt est (°C)",
    "Notes",
    "Data source",
]
PROCESS_FEATURES = FABRICATION_COLUMNS + ANNEALING_COLUMNS
NUMERIC_ANALYSIS_FEATURES = (
    GEOMETRY_COLUMNS
    + DERIVED_GEOMETRY_COLUMNS
    + PROCESS_FEATURES
    + STRESS_TEST_CURRENT_COLUMNS
    + COMPOSITION_FEATURES
)

CANONICAL_COLUMNS = (
    ["Composition", "Microwire", "Production datetime"]
    + GEOMETRY_COLUMNS
    + DERIVED_GEOMETRY_COLUMNS
    + FABRICATION_COLUMNS
    + ANNEALING_COLUMNS
    + STRESS_TEST_CURRENT_COLUMNS
    + FUNCTIONAL_COLUMNS
    + LEGACY_OUTCOME_COLUMNS
    + DERIVED_OUTCOME_COLUMNS
    + COMPOSITION_FEATURES
)

SUMMARY_TABLE_ORDER = [
    "coverage",
    "row_scope",
    "endpoint_coverage",
    "duplicate_wires",
    "composition_summary",
    "per_composition_process_strain_signals",
    "per_composition_process_fracture_strain_signals",
    "per_composition_process_stress_signals",
    "per_composition_process_fracture_stress_signals",
    "process_strain_correlations",
    "process_fracture_strain_correlations",
    "process_stress_correlations",
    "process_fracture_stress_correlations",
    "current_strain_correlations",
    "current_fracture_strain_correlations",
    "current_stress_correlations",
    "current_fracture_stress_correlations",
    "geometry_strain_correlations",
    "geometry_fracture_strain_correlations",
    "geometry_stress_correlations",
    "geometry_fracture_stress_correlations",
    "composition_strain_correlations",
    "composition_fracture_strain_correlations",
    "composition_stress_correlations",
    "composition_fracture_stress_correlations",
    "legacy_breakage_summary",
    "time_summary",
]

DEFAULT_FINDINGS_FILENAME = "microwire_eda_findings.json"
DEFAULT_FINDINGS_MD_FILENAME = "microwire_eda_findings.md"

HEADER_ALIASES = {
    "composition": "Composition",
    "zloženie": "Composition",
    "microwire": "Microwire",
    "date and time": "Production datetime",
    "production datetime": "Production datetime",
    "dátum a čas výroby": "Production datetime",
    "temperature (°c)": "Core temperature (°C)",
    "temperature [°c]": "Core temperature (°C)",
    "t 0c": "Core temperature (°C)",
    "core temperature (°c)": "Core temperature (°C)",
    "glass temperature (°c)": "Glass temperature (°C)",
    "hmotnost": "Mass (g)",
    "mass": "Mass (g)",
    "winding speed (m/min)": "Winding speed (m/min)",
    "glass feeding (mm/min)": "Glass feeding (mm/min)",
    "underpressure": "Underpressure",
    "poznamka": "Notes",
    "notes": "Notes",
    "d (µm)": "d (µm)",
    "d (âµm)": "d (µm)",
    "d (μm)": "d (µm)",
    "d (î¼m)": "d (µm)",
    "d (µ)": "d (µm)",
    "d [µm]": "d (µm)",
    "d [um]": "d (µm)",
    "d_um": "d (µm)",
    "D (µm)": "D (µm)",
    "D (âµm)": "D (µm)",
    "D (μm)": "D (µm)",
    "D (î¼m)": "D (µm)",
    "D (µ)": "D (µm)",
    "D [µm]": "D (µm)",
    "D [um]": "D (µm)",
    "D_um": "D (µm)",
    "d/d": "d/D",
    "d / d": "d/D",
    "d_over_d": "d/D",
    "d_over_D": "d/D",
    "strain": "Strain",
    "legacy strain": "Legacy strain",
    "strain (%)": "Strain (%)",
    "fracture strain (%)": "Fracture strain (%)",
    "stress (mpa)": "Stress (MPa)",
    "legacy stress (mpa)": "Legacy stress (MPa)",
    "fracture stress (mpa)": "Fracture stress (MPa)",
    "brittle": "Brittle",
    "broke": "Broke",
    "broken": "is_broken",
    "is broken": "is_broken",
}

_DIAMETER_SUFFIX_ALIASES = {
    "(µm)",
    "(μm)",
    "(âµm)",
    "(î¼m)",
    "(µ)",
    "(um)",
    "[µm]",
    "[μm]",
    "[um]",
    "_um",
}

ProgressCallback = Callable[[str], None]


def _noop_progress(_message: str) -> None:
    return


def _build_casefold_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    for alias, target in HEADER_ALIASES.items():
        folded = alias.casefold()
        if folded in ambiguous:
            continue
        existing = aliases.get(folded)
        if existing is None or existing == target:
            aliases[folded] = target
            continue
        aliases.pop(folded, None)
        ambiguous.add(folded)
    return aliases


HEADER_ALIASES_CASEFOLD = _build_casefold_aliases()


@dataclass(slots=True)
class MicrowireEdaConfig:
    input_path: Path | None = None
    input_kind: str = INPUT_KIND_AUTO
    row_scope: str = ROW_SCOPE_ALL
    output_dir: Path | None = None
    report_title: str = "Microwire EDA Report"
    source_dataframe: pd.DataFrame | None = None
    filtered_row_indices: tuple[int, ...] = ()
    selected_row_indices: tuple[int, ...] = ()
    export_png_bundle: bool = True
    export_pdf_bundle: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    working_copy_dir: Path | None = None
    copied_project_path: Path | None = None
    copy_project: bool = True
    rebuild_project_if_assemble_missing: bool = True
    force_project_rebuild: bool = False
    aggregation_mode: str = AGGREGATION_RAW
    include_legacy_breakage_analysis: bool = True
    include_composition_splits: bool = True
    write_findings: bool = True


@dataclass(slots=True)
class FigureArtifact:
    key: str
    title: str
    section: str
    png_path: Path | None
    html: str


@dataclass(slots=True)
class MicrowireEdaAnalysis:
    config: MicrowireEdaConfig
    input_kind: str
    source_path: Path | None
    working_input_path: Path | None
    copied_project_path: Path | None
    used_project_rebuild: bool
    canonical_frame: pd.DataFrame
    scoped_frame: pd.DataFrame
    applied_scope: str
    row_counts: dict[str, int]
    tables: dict[str, pd.DataFrame]
    skipped_sections: dict[str, str]
    findings: list[dict[str, Any]]
    sufficiency_summary: dict[str, Any]
    endpoint_tables: dict[str, pd.DataFrame]


@dataclass(slots=True)
class MicrowireEdaResult:
    config: MicrowireEdaConfig
    input_kind: str
    output_dir: Path
    report_path: Path
    workbook_path: Path
    csv_path: Path
    manifest_path: Path
    pdf_path: Path | None
    figure_paths: list[Path]
    skipped_sections: dict[str, str]
    row_counts: dict[str, int]
    tables: dict[str, pd.DataFrame]
    findings_json_path: Path | None = None
    findings_md_path: Path | None = None
    copied_project_path: Path | None = None
    used_project_rebuild: bool = False
    sufficiency_summary: dict[str, Any] = field(default_factory=dict)
    endpoint_tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)


def _slugify(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = re.sub(r"[^0-9A-Za-z._-]+", "-", text).strip("-._")
    return text or "artifact"


def _header_key(value: object) -> str:
    return str(value).strip().replace("\ufeff", "").casefold()


def _canonical_diameter_alias(raw: str) -> str | None:
    text = str(raw).strip().replace("\ufeff", "")
    if not text:
        return None
    prefix = text[0]
    if prefix not in {"d", "D"}:
        return None
    suffix = re.sub(r"\s+", "", text[1:]).casefold()
    if suffix not in _DIAMETER_SUFFIX_ALIASES:
        return None
    return f"{prefix} (µm)"


def _parse_numeric(value: object) -> float:
    if value is None:
        return math.nan
    if isinstance(value, (int, float, np.number)):
        try:
            if math.isnan(float(value)):
                return math.nan
        except Exception:
            return float(value)
        return float(value)
    text = str(value).strip()
    if not text:
        return math.nan
    if text.upper() in {"#DIV/0!", "#N/A", "N/A", "NA", "NONE", "NULL"}:
        return math.nan
    text = text.replace(",", ".").replace("−", "-")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return math.nan
    try:
        return float(match.group(0))
    except ValueError:
        return math.nan


def _first_non_empty(series: pd.Series) -> object:
    for value in series.tolist():
        if value is None:
            continue
        if isinstance(value, float) and math.isnan(value):
            continue
        if str(value).strip() == "":
            continue
        return value
    return math.nan


def _parse_composition_parts(value: object) -> dict[str, float]:
    parts = {element: 0.0 for element in ["Mn", "Ni", "Fe", "Ga", "Co", "Cu", "Si", "Sn"]}
    text = str(value or "").strip()
    if not text:
        return parts
    for element, number in re.findall(r"([A-Z][a-z]?)(\d+(?:\.\d+)?)", text):
        if element in parts:
            parts[element] += float(number)
    return parts


def _wire_group_columns(frame: pd.DataFrame) -> list[str]:
    columns = [column for column in ["Composition", "Microwire"] if column in frame.columns]
    return columns if len(columns) == 2 else []


def _aggregate_repeated_measurements(frame: pd.DataFrame, mode: str) -> pd.DataFrame:
    if frame.empty or mode == AGGREGATION_RAW:
        return frame.reset_index(drop=True).copy()
    group_columns = _wire_group_columns(frame)
    if not group_columns:
        return frame.reset_index(drop=True).copy()
    if mode not in SUPPORTED_AGGREGATION_MODES:
        raise ValueError(f"Unsupported Microwire EDA aggregation mode: {mode}")

    outcome_max_columns = set(
        FUNCTIONAL_COLUMNS
        + DERIVED_OUTCOME_COLUMNS
        + STRESS_TEST_CURRENT_COLUMNS
        + ["is_broken"]
    )

    def _aggregate_column(series: pd.Series, *, column: str) -> object:
        if column in group_columns:
            return _first_non_empty(series)
        if pd.api.types.is_numeric_dtype(series):
            values = pd.to_numeric(series, errors="coerce").dropna()
            if values.empty:
                return math.nan
            if mode == AGGREGATION_PER_WIRE_MEDIAN:
                if column in {"is_broken", "Broke", "Brittle"}:
                    return float(values.max())
                return float(values.median())
            if column in outcome_max_columns or column in {"Broke", "Brittle"}:
                return float(values.max())
            return float(values.iloc[0])
        if pd.api.types.is_datetime64_any_dtype(series):
            values = series.dropna()
            return values.iloc[0] if not values.empty else pd.NaT
        return _first_non_empty(series)

    rows: list[dict[str, object]] = []
    for _, subset in frame.groupby(group_columns, dropna=True, sort=False):
        row: dict[str, object] = {}
        for column in frame.columns:
            row[str(column)] = _aggregate_column(subset[column], column=str(column))
        row["measurement_count"] = int(len(subset.index))
        rows.append(row)
    return pd.DataFrame(rows)


def _parse_boolish(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, np.integer)) and int(value) in {0, 1}:
        return int(value)
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if int(value) in {0, 1} and float(value).is_integer():
            return int(value)
    text = str(value).strip().casefold()
    if not text:
        return None
    if text in {"1", "true", "yes", "y", "broke", "broken", "brittle"}:
        return 1
    if text in {"0", "false", "no", "n", "ok", "okay"}:
        return 0
    return None


def _coerce_datetime(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Parsing dates in %Y-%m-%d %H:%M:%S format when dayfirst=True was specified.*",
            category=UserWarning,
        )
        parsed = pd.to_datetime(series, errors="coerce", dayfirst=True)
    if parsed.isna().all():
        return pd.to_datetime(series, errors="coerce")
    return parsed


def _copy_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        return pd.DataFrame()
    try:
        return frame.copy()
    except Exception:
        return pd.DataFrame(frame)


def detect_input_kind(path: Path | None, explicit: str = INPUT_KIND_AUTO) -> str:
    if explicit and explicit != INPUT_KIND_AUTO:
        return explicit
    if path is None:
        return INPUT_KIND_DATAFRAME
    suffix = path.suffix.lower()
    if suffix == ".pydpj":
        return INPUT_KIND_PROJECT
    if suffix in {".xlsx", ".xls", ".xlsm", ".csv"}:
        return INPUT_KIND_EXCEL
    raise ValueError(f"Unsupported Microwire EDA input: {path}")


def _project_section_to_frame(section: Mapping[str, Any]) -> pd.DataFrame:
    rows = section.get("rows")
    columns = section.get("columns")
    if isinstance(rows, list) and rows:
        if isinstance(rows[0], Mapping):
            return pd.DataFrame(rows)
        if isinstance(columns, list):
            return pd.DataFrame(rows, columns=[str(column) for column in columns])
    if isinstance(rows, list):
        return pd.DataFrame(rows)
    imported_rows = section.get("imported_rows")
    if isinstance(imported_rows, list) and imported_rows:
        if isinstance(imported_rows[0], Mapping):
            return pd.DataFrame(imported_rows)
        if isinstance(columns, list):
            return pd.DataFrame(imported_rows, columns=[str(column) for column in columns])
    return pd.DataFrame()


def _load_project_frame(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sections = payload.get("sections", {})
    if not isinstance(sections, Mapping):
        raise ValueError("Project file does not contain sections.")
    assemble = sections.get("assemble", {})
    if not isinstance(assemble, Mapping):
        raise ValueError("Project file does not contain an assemble section.")
    frame = _project_section_to_frame(assemble)
    if frame.empty:
        raise ValueError("Project file is missing assemble rows.")
    return frame


def _load_project_frame_if_available(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sections = payload.get("sections", {})
    if not isinstance(sections, Mapping):
        return pd.DataFrame()
    assemble = sections.get("assemble", {})
    if not isinstance(assemble, Mapping):
        return pd.DataFrame()
    return _project_section_to_frame(assemble)


def _rebuild_project_frame_via_builder(
    path: Path,
    *,
    progress_callback: ProgressCallback | None = None,
) -> pd.DataFrame:
    progress = progress_callback or _noop_progress
    progress("Rebuilding Assemble rows from project sections")

    previous_dialog_setting = os.environ.get("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS")
    os.environ["MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS"] = "1"
    try:
        from PyQt6 import QtWidgets
        from microwire_data_builder.core import BuilderConfig, DEFAULT_OUTPUT_NAME, _normalise_output_name, build_database
        from microwire_data_builder.ui import BuilderWindow, _apply_microscope_overrides

        app = QtWidgets.QApplication.instance()
        owns_app = app is None
        if app is None:
            app = QtWidgets.QApplication(["microwire_eda_rebuild"])

        window = BuilderWindow()
        try:
            window._load_project_from_path(path)
            assembly = getattr(window, "assembly_section", None)
            if assembly is None:
                raise ValueError("Builder project did not load an Assemble section.")

            selected_state = getattr(assembly, "_section_states", {})
            selected = set(selected_state.keys()) if isinstance(selected_state, dict) and selected_state else set(window.sections.keys())
            inputs = assembly._prepare_builder_inputs(selected, require_payloads=False)
            if inputs is None:
                raise ValueError("Builder project did not provide enough processed section data to rebuild Assemble rows.")

            (
                fabrication_index,
                annealing_records,
                vsm_hysteresis_records,
                vsm_temperature_records,
                dma_isostress_records,
                shape_memory_stress_strain_records,
                shape_memory_entries,
                fmr_records,
                microscope_index,
                video_index,
                strain_records,
                strain_entries,
                current_density_entries,
                overrides,
                phase_points,
                transition_points,
                video_overrides,
            ) = inputs

            if "microscope" in selected and overrides:
                microscope_index = _apply_microscope_overrides(microscope_index, overrides)
            elif "microscope" not in selected:
                microscope_index = {}

            output_dir = Path(tempfile.mkdtemp(prefix="microwire_eda_builder_"))
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
                output_name=_normalise_output_name(DEFAULT_OUTPUT_NAME),
            )
            build_kwargs = {
                "fabrication_index": fabrication_index,
                "measurement_records": annealing_records,
                "vsm_hysteresis_records": vsm_hysteresis_records if "vsm_hysteresis" in selected else [],
                "vsm_temperature_scan_records": vsm_temperature_records if "vsm_temperature_scan" in selected else [],
                "dma_iso_stress_records": dma_isostress_records if "dma_iso_stress" in selected else [],
                "shape_memory_stress_strain_records": (
                    shape_memory_stress_strain_records if "shape_memory_stress_strain" in selected else []
                ),
                "shape_memory_entries": shape_memory_entries if "shape_memory_stress_strain" in selected else {},
                "fmr_records": fmr_records if "fmr" in selected else [],
                "microscope_index": microscope_index if "microscope" in selected else {},
                "video_index": video_index if "videos" in selected else {},
                "video_overrides": video_overrides,
                "strain_records": strain_records if "strain" in selected else {},
                "strain_entries": strain_entries if "strain" in selected else {},
                "current_density_entries": current_density_entries if "current_density" in selected else {},
                "phase_points": phase_points,
                "transition_temps": transition_points,
                "skip_exports": True,
            }
            result = build_database(config, logger=window.logger, **build_kwargs)
            return _copy_frame(getattr(result, "dataframe", None))
        finally:
            try:
                window.close()
            except Exception:
                pass
            if owns_app and app is not None:
                try:
                    app.quit()
                except Exception:
                    pass
    finally:
        if previous_dialog_setting is None:
            os.environ.pop("MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS", None)
        else:
            os.environ["MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS"] = previous_dialog_setting


def _load_excel_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path)


def _normalise_output_dir(config: MicrowireEdaConfig) -> Path:
    if isinstance(config.output_dir, Path):
        return config.output_dir / _slugify(config.report_title)
    if isinstance(config.input_path, Path):
        return config.input_path.with_suffix("").parent / f"{_slugify(config.report_title)}_report"
    return Path.cwd() / f"{_slugify(config.report_title)}_report"


def _copy_project_for_analysis(
    source_path: Path,
    config: MicrowireEdaConfig,
) -> tuple[Path, Path | None]:
    if source_path.suffix.lower() != ".pydpj":
        return source_path, None
    if not config.copy_project and config.copied_project_path is None and config.working_copy_dir is None:
        return source_path, None

    if config.copied_project_path is not None:
        copied_path = config.copied_project_path.expanduser()
        copied_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        if config.working_copy_dir is not None:
            working_dir = config.working_copy_dir.expanduser()
            working_dir.mkdir(parents=True, exist_ok=True)
        elif config.output_dir is not None:
            working_dir = (_normalise_output_dir(config) / "_working_copy").resolve()
            working_dir.mkdir(parents=True, exist_ok=True)
        else:
            working_dir = Path(tempfile.mkdtemp(prefix="microwire_eda_"))
        copied_path = working_dir / f"{source_path.stem}-eda-copy{source_path.suffix}"
    shutil.copy2(source_path, copied_path)
    return copied_path, copied_path


def load_analysis_frame(
    config: MicrowireEdaConfig,
    progress_callback: ProgressCallback | None = None,
) -> tuple[pd.DataFrame, str, Path | None, Path | None, bool]:
    progress = progress_callback or _noop_progress
    if isinstance(config.source_dataframe, pd.DataFrame):
        return _copy_frame(config.source_dataframe), INPUT_KIND_DATAFRAME, config.input_path, None, False
    if config.input_path is None:
        raise ValueError("Microwire EDA requires an input path or source dataframe.")
    source_path = config.input_path.expanduser()
    kind = detect_input_kind(source_path, config.input_kind)
    working_path = source_path
    copied_project_path: Path | None = None
    used_project_rebuild = False
    if kind == INPUT_KIND_PROJECT:
        progress("Preparing disposable project copy")
        working_path, copied_project_path = _copy_project_for_analysis(source_path, config)
        frame = _load_project_frame_if_available(working_path)
        should_rebuild = config.force_project_rebuild or (
            config.rebuild_project_if_assemble_missing and frame.empty
        )
        if should_rebuild:
            frame = _rebuild_project_frame_via_builder(working_path, progress_callback=progress)
            used_project_rebuild = True
        if frame.empty:
            raise ValueError("Project file is missing assemble rows and could not be rebuilt.")
        return frame, kind, working_path, copied_project_path, used_project_rebuild
    if kind == INPUT_KIND_EXCEL:
        return _load_excel_frame(working_path), kind, working_path, None, False
    raise ValueError(f"Unsupported input kind: {kind}")


def load_input_frame(config: MicrowireEdaConfig) -> tuple[pd.DataFrame, str]:
    frame, kind, _working_path, _copied_project_path, _used_project_rebuild = load_analysis_frame(config)
    return frame, kind


def _canonical_column_names(columns: Iterable[object]) -> list[str]:
    output: list[str] = []
    for column in columns:
        raw = str(column).strip().replace("\ufeff", "")
        key = HEADER_ALIASES.get(raw)
        if key is None:
            key = _canonical_diameter_alias(raw)
        if key is None:
            key = HEADER_ALIASES_CASEFOLD.get(_header_key(raw), raw)
        output.append(key)
    return output


def _first_present(frame: pd.DataFrame, names: Sequence[str]) -> pd.Series | None:
    for name in names:
        if name in frame.columns:
            series = frame[name]
            if isinstance(series, pd.DataFrame):
                return series.iloc[:, 0]
            return series
    return None


def _dedupe_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or not frame.columns.duplicated().any():
        return frame
    deduped = pd.DataFrame(index=frame.index)
    for column in frame.columns:
        incoming = frame.loc[:, column]
        if isinstance(incoming, pd.DataFrame):
            series = incoming.iloc[:, 0]
            for idx in range(1, incoming.shape[1]):
                candidate = incoming.iloc[:, idx]
                series = series.where(~(series.isna() | (series == "")), candidate)
        else:
            series = incoming
        if column in deduped.columns:
            existing = deduped[column]
            deduped[column] = existing.where(~(existing.isna() | (existing == "")), series)
        else:
            deduped[column] = series
    return deduped


def canonicalise_frame(frame: pd.DataFrame) -> pd.DataFrame:
    clean = _copy_frame(frame)
    clean.columns = _canonical_column_names(clean.columns)
    clean = _dedupe_columns(clean)

    if "Strain (%)" not in clean.columns:
        source = _first_present(clean, ["Strain (%)", "Strain", "Legacy strain"])
        if source is not None:
            clean["Strain (%)"] = source
    if "Stress (MPa)" not in clean.columns:
        source = _first_present(clean, ["Stress (MPa)", "Legacy stress (MPa)"])
        if source is not None:
            clean["Stress (MPa)"] = source
    if "Legacy strain" not in clean.columns:
        source = _first_present(clean, ["Legacy strain", "Strain"])
        if source is not None:
            clean["Legacy strain"] = source
    if "Legacy stress (MPa)" not in clean.columns:
        source = _first_present(clean, ["Legacy stress (MPa)", "Stress (MPa)"])
        if source is not None:
            clean["Legacy stress (MPa)"] = source

    if "Production datetime" in clean.columns:
        clean["Production datetime"] = _coerce_datetime(clean["Production datetime"])

    numeric_candidates = set(
        GEOMETRY_COLUMNS
        + FABRICATION_COLUMNS
        + ANNEALING_COLUMNS
        + STRESS_TEST_CURRENT_COLUMNS
        + FUNCTIONAL_COLUMNS
    )
    for column in numeric_candidates:
        if column in clean.columns:
            clean[column] = clean[column].map(_parse_numeric)

    diameter = _first_present(clean, [GEOMETRY_COLUMNS[0]])
    outer_diameter = _first_present(clean, [GEOMETRY_COLUMNS[1]])
    ratio = _first_present(clean, ["d/D"])
    if diameter is not None and outer_diameter is not None:
        d_series = pd.to_numeric(diameter, errors="coerce")
        D_series = pd.to_numeric(outer_diameter, errors="coerce")
        if ratio is None or pd.to_numeric(ratio, errors="coerce").notna().sum() == 0:
            clean["d/D"] = d_series / D_series
        clean["coat_thickness_um"] = (D_series - d_series) / 2.0
        clean["core_fraction_linear"] = d_series / D_series
        clean["glass_area_fraction"] = 1.0 - (d_series / D_series) ** 2
        clean["metal_area_um2"] = math.pi * (d_series / 2.0) ** 2
        clean["glass_area_um2"] = math.pi * (D_series / 2.0) ** 2 - clean["metal_area_um2"]

    if "Composition" in clean.columns:
        composition_frame = clean["Composition"].map(_parse_composition_parts).apply(pd.Series)
        for column in composition_frame.columns:
            clean[column] = pd.to_numeric(composition_frame[column], errors="coerce")
        for element in ["Mn", "Co", "Cu", "Si", "Sn"]:
            clean[f"has_{element}"] = clean.get(element, pd.Series(dtype=float)).fillna(0.0).gt(0).astype(float)

    for column in LEGACY_OUTCOME_COLUMNS:
        if column in clean.columns:
            clean[column] = clean[column].map(_parse_boolish)

    explicit_broken = _first_present(clean, ["is_broken", "Broke", "Brittle"])
    strain_series = _first_present(clean, ["Strain (%)", "Strain", "Legacy strain"])
    fracture_strain_series = _first_present(clean, ["Fracture strain (%)"])

    derived_broken: list[int | float] = []
    for idx in range(len(clean.index)):
        broken_value = None
        if explicit_broken is not None:
            broken_value = _parse_boolish(explicit_broken.iloc[idx])
        strain_value = _parse_numeric(strain_series.iloc[idx]) if strain_series is not None else math.nan
        fracture_value = (
            _parse_numeric(fracture_strain_series.iloc[idx]) if fracture_strain_series is not None else math.nan
        )
        if broken_value is not None:
            derived_broken.append(broken_value)
        elif not math.isnan(strain_value):
            derived_broken.append(0)
        elif math.isnan(strain_value) and not math.isnan(fracture_value):
            derived_broken.append(1)
        else:
            derived_broken.append(math.nan)
    clean["is_broken"] = derived_broken

    if strain_series is not None:
        clean["strain_abs"] = strain_series.map(lambda value: abs(_parse_numeric(value)))
    else:
        clean["strain_abs"] = math.nan
    if fracture_strain_series is not None:
        clean["fracture_strain_abs"] = fracture_strain_series.map(lambda value: abs(_parse_numeric(value)))
    else:
        clean["fracture_strain_abs"] = math.nan
    return clean


def _safe_iloc(frame: pd.DataFrame, indices: Sequence[int]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    valid = [idx for idx in indices if 0 <= int(idx) < len(frame.index)]
    if not valid:
        return frame.iloc[0:0].copy()
    return frame.iloc[valid].reset_index(drop=True)


def apply_row_scope(frame: pd.DataFrame, config: MicrowireEdaConfig) -> tuple[pd.DataFrame, str]:
    scope = config.row_scope or ROW_SCOPE_ALL
    if scope == ROW_SCOPE_SELECTED and config.selected_row_indices:
        return _safe_iloc(frame, config.selected_row_indices), ROW_SCOPE_SELECTED
    if scope == ROW_SCOPE_FILTERED and config.filtered_row_indices:
        return _safe_iloc(frame, config.filtered_row_indices), ROW_SCOPE_FILTERED
    return frame.reset_index(drop=True).copy(), ROW_SCOPE_ALL


def _ordered_columns(frame: pd.DataFrame) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for column in CANONICAL_COLUMNS:
        if column in frame.columns and column not in seen:
            ordered.append(column)
            seen.add(column)
    for column in frame.columns:
        text = str(column)
        if text not in seen:
            ordered.append(text)
            seen.add(text)
    return ordered


def _ordered_export_frame(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = [column for column in CANONICAL_COLUMNS if column in frame.columns]
    remaining = [column for column in frame.columns if column not in ordered]
    return frame.loc[:, ordered + remaining].copy()


def _table_html(frame: pd.DataFrame, *, index: bool = False) -> str:
    if frame.empty:
        return "<p class='empty-note'>No rows were available for this table.</p>"
    return frame.copy().to_html(index=index, border=0, classes="eda-table")


def _figure_to_html(fig: plt.Figure) -> str:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=96)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f'<img alt="EDA figure" src="data:image/png;base64,{encoded}" />'


def _save_figure(
    fig: plt.Figure,
    *,
    output_dir: Path,
    key: str,
    title: str,
    section: str,
    pdf: PdfPages | None,
    write_png: bool = True,
) -> FigureArtifact:
    png_path: Path | None = None
    if write_png:
        output_dir.mkdir(parents=True, exist_ok=True)
        png_path = output_dir / f"{_slugify(key)}.png"
        fig.savefig(png_path, dpi=180, bbox_inches="tight")
    if pdf is not None:
        pdf.savefig(fig, bbox_inches="tight")
    html_text = _figure_to_html(fig)
    plt.close(fig)
    return FigureArtifact(key=key, title=title, section=section, png_path=png_path, html=html_text)


def _blank_figure(title: str, message: str) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.axis("off")
    ax.text(0.5, 0.62, title, ha="center", va="center", fontsize=14, fontweight="bold")
    ax.text(0.5, 0.4, message, ha="center", va="center", fontsize=11, wrap=True)
    return fig


def _count_notna(frame: pd.DataFrame, column: str) -> int:
    return int(frame.get(column, pd.Series(dtype=float)).notna().sum())


def _row_counts(frame: pd.DataFrame) -> dict[str, int]:
    unique_wires = 0
    group_columns = _wire_group_columns(frame)
    if group_columns:
        unique_wires = int(frame.loc[:, group_columns].dropna().drop_duplicates().shape[0])
    return {
        "all_rows": int(len(frame.index)),
        "unique_wires": unique_wires,
        "known_outcome": int(frame.get("is_broken", pd.Series(dtype=float)).notna().sum()),
        "numeric_strain": _count_notna(frame, "strain_abs"),
        "numeric_fracture_strain": _count_notna(frame, "fracture_strain_abs"),
        "numeric_stress": _count_notna(frame, "Stress (MPa)"),
        "numeric_fracture_stress": _count_notna(frame, "Fracture stress (MPa)"),
        "dated_rows": _count_notna(frame, "Production datetime"),
        "composition_rows": _count_notna(frame, "Composition"),
    }


def _report_relevant_columns(frame: pd.DataFrame) -> list[str]:
    preferred = list(dict.fromkeys(list(CANONICAL_COLUMNS) + REPORT_EXTRA_COLUMNS))
    output = [column for column in preferred if column in frame.columns]
    if output:
        return output
    return _ordered_columns(frame)


def _coverage_table(frame: pd.DataFrame, *, columns: Sequence[str] | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    total = max(len(frame.index), 1)
    for column in columns or _ordered_columns(frame):
        series = frame[column]
        non_null = int(pd.Series(series).notna().sum())
        rows.append(
            {
                "column": column,
                "non_null": non_null,
                "null": int(total - non_null),
                "coverage_ratio": round(non_null / total, 3),
            }
        )
    return pd.DataFrame(rows)


def _row_scope_table(requested: str, applied: str, counts: Mapping[str, int], aggregation_mode: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"metric": "requested_scope", "value": requested},
            {"metric": "applied_scope", "value": applied},
            {"metric": "aggregation_mode", "value": aggregation_mode},
            {"metric": "rows", "value": counts.get("all_rows", 0)},
            {"metric": "unique_wires", "value": counts.get("unique_wires", 0)},
            {"metric": "numeric_strain", "value": counts.get("numeric_strain", 0)},
            {"metric": "numeric_fracture_strain", "value": counts.get("numeric_fracture_strain", 0)},
            {"metric": "numeric_stress", "value": counts.get("numeric_stress", 0)},
            {"metric": "numeric_fracture_stress", "value": counts.get("numeric_fracture_stress", 0)},
        ]
    )


def _endpoint_coverage_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    total = max(len(frame.index), 1)
    for column, label in [
        ("strain_abs", "|Strain|"),
        ("fracture_strain_abs", "|Fracture strain|"),
        ("Stress (MPa)", "Stress (MPa)"),
        ("Fracture stress (MPa)", "Fracture stress (MPa)"),
        ("is_broken", "Legacy breakage label"),
    ]:
        non_null = int(frame.get(column, pd.Series(dtype=float)).notna().sum())
        rows.append(
            {
                "endpoint": label,
                "column": column,
                "rows_available": non_null,
                "coverage_ratio": round(non_null / total, 3),
            }
        )
    return pd.DataFrame(rows)


def _duplicate_wire_table(frame: pd.DataFrame) -> pd.DataFrame:
    if "Composition" not in frame.columns or "Microwire" not in frame.columns:
        return pd.DataFrame(columns=["Composition", "Microwire", "count"])
    grouped = (
        frame.loc[:, ["Composition", "Microwire"]]
        .dropna()
        .groupby(["Composition", "Microwire"], dropna=True)
        .size()
        .reset_index(name="count")
    )
    duplicates = grouped.loc[grouped["count"] > 1].sort_values(["count", "Composition", "Microwire"], ascending=[False, True, True])
    return duplicates.reset_index(drop=True)


def _composition_summary_table(frame: pd.DataFrame) -> pd.DataFrame:
    if "Composition" not in frame.columns:
        return pd.DataFrame()
    grouped = frame.groupby("Composition", dropna=True)
    summary = grouped.size().reset_index(name="rows")
    if "Microwire" in frame.columns:
        summary = summary.merge(
            grouped["Microwire"].nunique().reset_index(name="unique_wires"),
            on="Composition",
            how="left",
        )
    for source, count_name, mean_name in [
        ("strain_abs", "numeric_strain", "mean_strain"),
        ("fracture_strain_abs", "numeric_fracture_strain", "mean_fracture_strain"),
        ("Stress (MPa)", "numeric_stress", "mean_stress"),
        ("Fracture stress (MPa)", "numeric_fracture_stress", "mean_fracture_stress"),
    ]:
        if source not in frame.columns:
            continue
        count_table = grouped[source].apply(lambda series: int(pd.Series(series).notna().sum())).reset_index(name=count_name)
        mean_table = grouped[source].mean().reset_index(name=mean_name)
        summary = summary.merge(count_table, on="Composition", how="left")
        summary = summary.merge(mean_table, on="Composition", how="left")
    summary = summary.sort_values(["rows", "Composition"], ascending=[False, True])
    return summary


def _time_summary_table(frame: pd.DataFrame) -> pd.DataFrame:
    if "Production datetime" not in frame.columns:
        return pd.DataFrame()
    working = frame.loc[frame["Production datetime"].notna()].copy()
    if working.empty:
        return pd.DataFrame()
    working["month"] = working["Production datetime"].dt.to_period("M").astype(str)
    grouped = working.groupby("month")
    summary = grouped.size().reset_index(name="rows")
    for source, name in [
        ("strain_abs", "numeric_strain"),
        ("fracture_strain_abs", "numeric_fracture_strain"),
        ("Stress (MPa)", "numeric_stress"),
        ("Fracture stress (MPa)", "numeric_fracture_stress"),
    ]:
        if source not in working.columns:
            continue
        count_table = grouped[source].apply(lambda series: int(pd.Series(series).notna().sum())).reset_index(name=name)
        summary = summary.merge(count_table, on="month", how="left")
    return summary


def _safe_spearman(frame: pd.DataFrame, left: str, right: str) -> tuple[float, float, int]:
    subset = frame[[left, right]].dropna()
    if len(subset.index) < 3:
        return math.nan, math.nan, int(len(subset.index))
    if int(subset[left].nunique()) < 2 or int(subset[right].nunique()) < 2:
        return math.nan, math.nan, int(len(subset.index))
    rho, p_value = stats.spearmanr(subset[left], subset[right])
    return float(rho), float(p_value), int(len(subset.index))


def _target_correlation_table(frame: pd.DataFrame, *, target: str, features: Sequence[str]) -> pd.DataFrame:
    if target not in frame.columns:
        return pd.DataFrame(columns=["feature", "rho", "abs_rho", "p_value", "n"])
    rows: list[dict[str, Any]] = []
    for feature in features:
        if feature not in frame.columns or feature == target:
            continue
        rho, p_value, n = _safe_spearman(frame, feature, target)
        if n < 3:
            continue
        rows.append(
            {
                "feature": feature,
                "rho": rho,
                "abs_rho": abs(rho) if not math.isnan(rho) else math.nan,
                "p_value": p_value,
                "n": n,
            }
        )
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["abs_rho", "feature"], ascending=[False, True]).reset_index(drop=True)
    return result


def _per_composition_signal_table(
    frame: pd.DataFrame,
    *,
    target: str,
    features: Sequence[str],
    top_n: int = 3,
) -> pd.DataFrame:
    if "Composition" not in frame.columns or target not in frame.columns:
        return pd.DataFrame(columns=["Composition", "feature", "rho", "abs_rho", "p_value", "n"])
    rows: list[dict[str, Any]] = []
    for composition, subset in frame.groupby("Composition", dropna=True):
        table = _target_correlation_table(subset, target=target, features=features)
        if table.empty:
            continue
        for record in table.head(top_n).to_dict(orient="records"):
            rows.append({"Composition": composition, **record})
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["Composition", "abs_rho", "feature"], ascending=[True, False, True]).reset_index(drop=True)
    return result


def _legacy_breakage_summary(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.loc[frame["is_broken"].isin([0, 1])].copy()
    rows: list[dict[str, Any]] = []
    if working.empty:
        return pd.DataFrame(rows)
    for feature in NUMERIC_ANALYSIS_FEATURES + ["Stress (MPa)", "Fracture stress (MPa)"]:
        if feature not in working.columns:
            continue
        ok_values = working.loc[working["is_broken"] == 0, feature].dropna()
        broke_values = working.loc[working["is_broken"] == 1, feature].dropna()
        if len(ok_values.index) < 2 or len(broke_values.index) < 2:
            continue
        try:
            _stat, p_value = stats.mannwhitneyu(ok_values, broke_values, alternative="two-sided")
        except Exception:
            p_value = math.nan
        rows.append(
            {
                "feature": feature,
                "ok_mean": float(ok_values.mean()),
                "broke_mean": float(broke_values.mean()),
                "delta_broke_minus_ok": float(broke_values.mean() - ok_values.mean()),
                "p_value": p_value,
                "n_ok": int(len(ok_values.index)),
                "n_broke": int(len(broke_values.index)),
            }
        )
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("p_value", na_position="last").reset_index(drop=True)
    return result


def _status_series(frame: pd.DataFrame) -> pd.Series:
    status = pd.Series(STATUS_NO_DATA, index=frame.index, dtype=object)
    if "is_broken" not in frame.columns:
        return status
    status.loc[frame["is_broken"].eq(0)] = STATUS_OK
    status.loc[frame["is_broken"].eq(1)] = STATUS_BROKE
    return status


def _available_numeric_columns(
    frame: pd.DataFrame,
    *,
    min_non_null: int = 4,
    exclude: Sequence[str] = (),
) -> list[str]:
    excluded = set(exclude)
    output: list[str] = []
    for column in frame.columns:
        if column in excluded:
            continue
        series = pd.to_numeric(frame[column], errors="coerce")
        if int(series.notna().sum()) >= min_non_null:
            output.append(str(column))
    return output


def _coverage_figure(frame: pd.DataFrame) -> plt.Figure:
    columns = [column for column in CANONICAL_COLUMNS if column in frame.columns]
    if not columns:
        return _blank_figure("Coverage report", "No canonical columns are available.")
    coverage_frame = frame.loc[:, columns].copy().head(150)
    matrix = coverage_frame.notna().astype(int).T.values
    fig, ax = plt.subplots(figsize=(max(10, len(coverage_frame.index) * 0.14), max(6, len(columns) * 0.28)))
    image = ax.imshow(
        matrix,
        aspect="auto",
        cmap=ListedColormap(["#d32f2f", "#43a047"]),
        interpolation="nearest",
        vmin=0,
        vmax=1,
    )
    ax.set_yticks(range(len(columns)))
    ax.set_yticklabels(columns)
    ax.set_xticks(range(len(coverage_frame.index)))
    ax.set_xticklabels(range(len(coverage_frame.index)), fontsize=7)
    ax.set_xlabel("Row")
    ax.set_title("Coverage heatmap")
    cbar = fig.colorbar(image, ax=ax, ticks=[0, 1])
    cbar.ax.set_yticklabels(["missing", "present"])
    fig.tight_layout()
    return fig


def _endpoint_overview_figure(frame: pd.DataFrame) -> plt.Figure:
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    axes = axes.ravel()
    endpoint_specs = [
        ("strain_abs", "|Strain| distribution", "#4f8bc9"),
        ("fracture_strain_abs", "|Fracture strain| distribution", "#e39d34"),
        ("Stress (MPa)", "Stress distribution", "#8f63c8"),
        ("Fracture stress (MPa)", "Fracture stress distribution", "#26a69a"),
    ]
    for axis, (column, title, color) in zip(axes, endpoint_specs, strict=False):
        series = frame.get(column, pd.Series(dtype=float)).dropna()
        if series.empty:
            axis.text(0.5, 0.5, "No data", ha="center", va="center")
            axis.set_axis_off()
            continue
        axis.hist(series, bins=min(10, max(4, len(series))), color=color, edgecolor="white")
        axis.axvline(series.median(), color="#263238", linestyle="--", linewidth=1.2)
        axis.set_title(title)
    fig.tight_layout()
    return fig


def _composition_coverage_figure(frame: pd.DataFrame) -> plt.Figure:
    summary = _composition_summary_table(frame).head(12)
    if summary.empty:
        return _blank_figure("Composition coverage", "No composition labels were available.")
    labels = summary["Composition"].tolist()
    fig, axes = plt.subplots(1, 2, figsize=(14, max(4.5, len(labels) * 0.45)))
    axes[0].barh(labels[::-1], summary["rows"].tolist()[::-1], color="#4f8bc9")
    axes[0].set_title("Rows per composition")
    axes[0].set_xlabel("Rows")
    endpoint_labels = [
        column
        for column in ["numeric_strain", "numeric_fracture_strain", "numeric_stress", "numeric_fracture_stress"]
        if column in summary.columns
    ]
    if not endpoint_labels:
        return _blank_figure("Composition coverage", "No endpoint coverage columns were available.")
    matrix = summary.loc[:, endpoint_labels].to_numpy()[::-1]
    image = axes[1].imshow(matrix, aspect="auto", cmap="YlGnBu")
    axes[1].set_yticks(range(len(labels)))
    axes[1].set_yticklabels(labels[::-1])
    axes[1].set_xticks(range(len(endpoint_labels)))
    readable_labels = {
        "numeric_strain": "Strain",
        "numeric_fracture_strain": "Fracture strain",
        "numeric_stress": "Stress",
        "numeric_fracture_stress": "Fracture stress",
    }
    axes[1].set_xticklabels([readable_labels.get(label, label) for label in endpoint_labels], rotation=20, ha="right")
    axes[1].set_title("Endpoint availability by composition")
    fig.colorbar(image, ax=axes[1], shrink=0.85)
    fig.tight_layout()
    return fig


def _correlation_bar_figure(table: pd.DataFrame, *, title: str) -> plt.Figure:
    if table.empty:
        return _blank_figure(title, "Not enough rows for this correlation view.")
    display = table.head(8).iloc[::-1]
    colors = ["#2e8b57" if value >= 0 else "#d94f4f" for value in display["rho"]]
    fig, ax = plt.subplots(figsize=(9, max(4, len(display) * 0.65)))
    ax.barh(display["feature"], display["rho"], color=colors)
    ax.axvline(0, color="black", linewidth=1)
    ax.set_xlabel("Spearman rho")
    ax.set_title(title)
    fig.tight_layout()
    return fig


def _scatter_grid(frame: pd.DataFrame, *, target: str, features: Sequence[str], title: str) -> plt.Figure:
    usable_features = [feature for feature in features if feature in frame.columns]
    if target not in frame.columns or not usable_features:
        return _blank_figure(title, "Not enough data for scatter plots.")
    rows = math.ceil(len(usable_features) / 2)
    fig, axes = plt.subplots(rows, 2, figsize=(12, max(4, rows * 3.4)))
    axes_array = np.atleast_1d(axes).ravel()
    status = _status_series(frame)
    color_values = status.map(STATUS_COLORS).fillna("#9c9c9c")
    target_series = pd.to_numeric(frame[target], errors="coerce")
    for ax, feature in zip(axes_array, usable_features, strict=False):
        feature_series = pd.to_numeric(frame.get(feature), errors="coerce")
        mask = feature_series.notna() & target_series.notna()
        if int(mask.sum()) < 2:
            ax.axis("off")
            continue
        ax.scatter(
            feature_series[mask],
            target_series[mask],
            c=color_values[mask],
            alpha=0.85,
            edgecolors="white",
            linewidths=0.5,
        )
        try:
            slope, intercept = np.polyfit(feature_series[mask], target_series[mask], 1)
            xs = np.linspace(float(feature_series[mask].min()), float(feature_series[mask].max()), 100)
            ax.plot(xs, slope * xs + intercept, color="#8e5ea2", linestyle="--", linewidth=1)
        except Exception:
            pass
        ax.set_title(feature)
        ax.set_xlabel(feature)
        ax.set_ylabel(target)
    for ax in axes_array[len(usable_features):]:
        ax.axis("off")
    fig.suptitle(title)
    fig.tight_layout()
    return fig


def _pairplot_figure(frame: pd.DataFrame) -> plt.Figure:
    candidates = _available_numeric_columns(
        frame,
        min_non_null=8,
        exclude=["is_broken"],
    )
    columns = candidates[:4]
    if len(columns) < 3:
        return _blank_figure("Pairplot", "Not enough overlapping numeric rows for a pairplot.")
    working = frame[columns].dropna(subset=columns, how="all").copy()
    if len(working.index) < 6:
        return _blank_figure("Pairplot", "Not enough overlapping numeric rows for a pairplot.")
    axis_array = scatter_matrix(
        working[columns],
        figsize=(2.6 * len(columns), 2.6 * len(columns)),
        diagonal="hist",
        alpha=0.65,
        color="#5b9bd5",
    )
    fig = axis_array[0, 0].figure
    fig.suptitle("Pairplot of available numeric columns", fontsize=14)
    fig.tight_layout()
    return fig


def _parallel_coordinates_figure(frame: pd.DataFrame) -> plt.Figure:
    columns = [column for column in NUMERIC_ANALYSIS_FEATURES if column in frame.columns]
    if len(columns) < 4:
        return _blank_figure("Parallel coordinates", "Not enough numeric columns for parallel coordinates.")
    band_target = "fracture_strain_abs"
    if band_target not in frame.columns or int(frame[band_target].notna().sum()) < 6:
        band_target = "strain_abs"
    if band_target not in frame.columns or int(frame[band_target].notna().sum()) < 6:
        return _blank_figure("Parallel coordinates", "Not enough endpoint rows for parallel coordinates.")
    working = frame.loc[:, columns + [band_target]].dropna(subset=columns, how="any")
    if len(working.index) < 6:
        return _blank_figure("Parallel coordinates", "Not enough complete rows for parallel coordinates.")
    normalized = working.copy()
    for column in columns:
        minimum = float(normalized[column].min())
        maximum = float(normalized[column].max())
        normalized[column] = 0.5 if math.isclose(minimum, maximum) else (normalized[column] - minimum) / (maximum - minimum)
    band_source = normalized[band_target]
    if band_source.isna().all() or band_source.nunique(dropna=True) < 2:
        normalized["Band"] = "All rows"
    else:
        normalized["Band"] = pd.qcut(
            band_source,
            q=min(3, int(band_source.nunique(dropna=True))),
            duplicates="drop",
        ).astype(str)
    fig, ax = plt.subplots(figsize=(11, 5))
    parallel_coordinates(normalized.drop(columns=[band_target]), "Band", alpha=0.35, ax=ax)
    ax.set_ylabel("Normalised value (0-1)")
    ax.set_title("Parallel coordinates")
    fig.tight_layout()
    return fig


def _time_drift_figure(frame: pd.DataFrame) -> plt.Figure:
    if "Production datetime" not in frame.columns:
        return _blank_figure("Time drift", "No usable production timeline rows were available.")
    working = frame.loc[frame["Production datetime"].notna()].sort_values("Production datetime").copy()
    if working.empty:
        return _blank_figure("Time drift", "No usable production timeline rows were available.")
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), sharex=True)
    specs = [
        ("strain_abs", "|Strain| over time", "#1e88e5"),
        ("fracture_strain_abs", "|Fracture strain| over time", "#fb8c00"),
        ("Stress (MPa)", "Stress over time", "#8e24aa"),
        ("Fracture stress (MPa)", "Fracture stress over time", "#00897b"),
    ]
    for ax, (column, title, color) in zip(axes.ravel(), specs, strict=False):
        subset = working.loc[working[column].notna()] if column in working.columns else pd.DataFrame()
        if subset.empty:
            ax.text(0.5, 0.5, "No data", ha="center", va="center")
            ax.set_axis_off()
            continue
        ax.scatter(subset["Production datetime"], subset[column], color=color, alpha=0.8)
        ax.set_title(title)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.subplots_adjust(bottom=0.16, hspace=0.38, wspace=0.28)
    return fig


def _format_count(value: int, total: int) -> str:
    if total <= 0:
        return f"{value}"
    return f"{value}/{total} ({value / total:.0%})"


def _summarise_table(table: pd.DataFrame, *, limit: int = 3) -> list[dict[str, Any]]:
    if table.empty:
        return []
    records = table.head(limit).replace({np.nan: None}).to_dict(orient="records")
    return [dict(record) for record in records]


def _generate_findings(
    frame: pd.DataFrame,
    *,
    config: MicrowireEdaConfig,
    counts: Mapping[str, int],
    tables: Mapping[str, pd.DataFrame],
    include_legacy_breakage_analysis: bool,
    include_composition_splits: bool,
    used_project_rebuild: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    total_rows = int(counts.get("all_rows", 0))

    def _preferred_top_row(table: pd.DataFrame, preferred_features: Sequence[str] | None = None) -> pd.Series:
        if preferred_features:
            preferred = table.loc[table["feature"].isin(preferred_features)]
            if not preferred.empty:
                return preferred.iloc[0]
        return table.iloc[0]

    findings.append(
        {
            "category": "data_quality",
            "headline": "Endpoint coverage is sparse and uneven.",
            "detail": (
                f"Rows analysed: {total_rows}. Available rows are "
                f"{_format_count(int(counts.get('numeric_strain', 0)), total_rows)} for strain, "
                f"{_format_count(int(counts.get('numeric_fracture_strain', 0)), total_rows)} for fracture strain, "
                f"{_format_count(int(counts.get('numeric_stress', 0)), total_rows)} for stress, and "
                f"{_format_count(int(counts.get('numeric_fracture_stress', 0)), total_rows)} for fracture stress."
            ),
            "evidence": {
                "row_counts": dict(counts),
            },
            "confidence": "high",
        }
    )

    if used_project_rebuild:
        findings.append(
            {
                "category": "data_quality",
                "headline": "Assemble rows were rebuilt transiently from the project sections.",
                "detail": "This run did not rely only on already-saved Assemble rows. Microwire EDA rebuilt the assembled dataframe in-process from the Builder project sections, without mutating the source project file.",
                "evidence": {},
                "confidence": "high",
            }
        )

    if config.aggregation_mode != AGGREGATION_RAW:
        mode_label = {
            AGGREGATION_PER_WIRE_MEDIAN: "per-wire median",
            AGGREGATION_PER_WIRE_BEST: "per-wire best",
        }.get(config.aggregation_mode, config.aggregation_mode)
        findings.append(
            {
                "category": "data_quality",
                "headline": f"Repeated measurements were aggregated in {mode_label} mode.",
                "detail": (
                    f"This run collapses duplicate Composition+Microwire rows before ranking signals. "
                    f"Rows analysed after aggregation: {total_rows}; unique wires: {int(counts.get('unique_wires', 0))}."
                ),
                "evidence": {
                    "aggregation_mode": config.aggregation_mode,
                    "unique_wires": int(counts.get("unique_wires", 0)),
                },
                "confidence": "high",
            }
        )

    duplicates = tables.get("duplicate_wires", pd.DataFrame())
    if isinstance(duplicates, pd.DataFrame) and not duplicates.empty:
        findings.append(
            {
                "category": "data_quality",
                "headline": "Duplicate composition/microwire labels are present.",
                "detail": "Some composition and microwire keys appear more than once in the analysed dataset. These rows should be checked before treating per-wire trends as independent evidence.",
                "evidence": {"examples": _summarise_table(duplicates)},
                "confidence": "medium",
            }
        )

    for label, table_key in [
        ("strain", "process_strain_correlations"),
        ("fracture strain", "process_fracture_strain_correlations"),
        ("stress", "process_stress_correlations"),
        ("fracture stress", "process_fracture_stress_correlations"),
    ]:
        table = tables.get(table_key, pd.DataFrame())
        if not isinstance(table, pd.DataFrame) or table.empty:
            findings.append(
                {
                    "category": "endpoint_signal",
                    "headline": f"{label.title()} trends remain underpowered.",
                    "detail": f"There are not enough overlapping rows to support a stable process-to-{label} ranking yet.",
                    "evidence": {"table": table_key},
                    "confidence": "high",
                }
            )
            continue
        top_row = _preferred_top_row(table, FABRICATION_COLUMNS)
        direction = "higher" if float(top_row["rho"]) >= 0 else "lower"
        headline = f"Top process signal for {label}: {top_row['feature']}."
        detail_prefix = "The strongest available process-side monotonic association"
        confidence = "medium"
        if int(top_row["n"]) < 6:
            headline = f"Preliminary process signal for {label}: {top_row['feature']}."
            detail_prefix = "The strongest currently available preliminary process-side monotonic association"
            confidence = "low"
        findings.append(
            {
                "category": "endpoint_signal",
                "headline": headline,
                "detail": (
                    f"{detail_prefix} with {label} is {top_row['feature']} "
                    f"(Spearman rho={float(top_row['rho']):.3f}, n={int(top_row['n'])}). In this dataset, "
                    f"{direction} values of that variable track higher {label} values."
                ),
                "evidence": {"top_rows": _summarise_table(table)},
                "confidence": confidence,
            }
        )

    for label, table_key in [
        ("strain", "current_strain_correlations"),
        ("fracture strain", "current_fracture_strain_correlations"),
        ("stress", "current_stress_correlations"),
        ("fracture stress", "current_fracture_stress_correlations"),
    ]:
        table = tables.get(table_key, pd.DataFrame())
        if not isinstance(table, pd.DataFrame) or table.empty:
            continue
        top_row = table.iloc[0]
        findings.append(
            {
                "category": "derived_signal",
                "headline": f"Top current-side signal for {label}: {top_row['feature']}.",
                "detail": (
                    f"The strongest current or current-density association with {label} is {top_row['feature']} "
                    f"(Spearman rho={float(top_row['rho']):.3f}, n={int(top_row['n'])}). "
                    "Interpret current-density signals carefully because they partly inherit diameter algebra."
                ),
                "evidence": {"top_rows": _summarise_table(table)},
                "confidence": "low" if int(top_row["n"]) < 6 else "medium",
            }
        )
        break

    composition_signal = tables.get("composition_fracture_stress_correlations", pd.DataFrame())
    if isinstance(composition_signal, pd.DataFrame) and not composition_signal.empty:
        top_row = composition_signal.iloc[0]
        findings.append(
            {
                "category": "cohorts",
                "headline": f"Top parsed-composition signal for fracture stress: {top_row['feature']}.",
                "detail": (
                    f"The strongest available parsed-composition association with fracture stress is {top_row['feature']} "
                    f"(Spearman rho={float(top_row['rho']):.3f}, n={int(top_row['n'])}). "
                    "Treat elemental signals cautiously because composition families are imbalanced and element percentages are not independent."
                ),
                "evidence": {"top_rows": _summarise_table(composition_signal)},
                "confidence": "low" if int(top_row["n"]) < 8 else "medium",
            }
        )

    composition_summary = tables.get("composition_summary", pd.DataFrame())
    if include_composition_splits and isinstance(composition_summary, pd.DataFrame) and not composition_summary.empty:
        richest = composition_summary.iloc[0]
        findings.append(
            {
                "category": "cohorts",
                "headline": f"Composition coverage is concentrated in {richest['Composition']}.",
                "detail": (
                    f"The densest composition in the current dataset is {richest['Composition']} with "
                    f"{int(richest['rows'])} rows. Most composition-specific conclusions should be treated as cohort-specific "
                    f"until more families have comparable endpoint coverage."
                ),
                "evidence": {"top_rows": _summarise_table(composition_summary)},
                "confidence": "high",
            }
        )
        per_comp = tables.get("per_composition_process_strain_signals", pd.DataFrame())
        if isinstance(per_comp, pd.DataFrame) and not per_comp.empty:
            strongest = per_comp.sort_values(["abs_rho", "Composition"], ascending=[False, True]).iloc[0]
            direction = "higher" if float(strongest["rho"]) >= 0 else "lower"
            confidence = "medium" if int(strongest["n"]) >= 6 else "low"
            headline = f"Strongest composition-specific strain signal: {strongest['Composition']} / {strongest['feature']}."
            detail_prefix = "Within"
            if int(strongest["n"]) < 6:
                headline = f"Preliminary composition-specific strain signal: {strongest['Composition']} / {strongest['feature']}."
                detail_prefix = "Within"
            findings.append(
                {
                    "category": "cohorts",
                    "headline": headline,
                    "detail": (
                        f"{detail_prefix} {strongest['Composition']}, the strongest available process-to-strain relationship is "
                        f"{strongest['feature']} (Spearman rho={float(strongest['rho']):.3f}, n={int(strongest['n'])}). "
                        f"In that family, {direction} values of the feature track higher strain."
                    ),
                    "evidence": {"top_rows": _summarise_table(per_comp)},
                    "confidence": confidence,
                }
            )

    if include_legacy_breakage_analysis and int(counts.get("known_outcome", 0)) > 0:
        findings.append(
            {
                "category": "legacy_context",
                "headline": "Legacy breakage labels are available, but they are no longer the main analysis target.",
                "detail": (
                    f"Breakage labels are present for {_format_count(int(counts.get('known_outcome', 0)), total_rows)} rows. "
                    "They remain useful as auxiliary context, but modern measured strain and fracture endpoints are preferred whenever available."
                ),
                "evidence": {"legacy_rows": int(counts.get("known_outcome", 0))},
                "confidence": "high",
            }
        )

    sufficiency_summary = {
        "all_rows": total_rows,
        "sufficient_for_correlation": {
            "strain_abs": int(counts.get("numeric_strain", 0)) >= 6,
            "fracture_strain_abs": int(counts.get("numeric_fracture_strain", 0)) >= 6,
            "Stress (MPa)": int(counts.get("numeric_stress", 0)) >= 6,
            "Fracture stress (MPa)": int(counts.get("numeric_fracture_stress", 0)) >= 6,
        },
        "composition_split_viable": bool(
            isinstance(composition_summary, pd.DataFrame) and not composition_summary.empty and int(composition_summary["rows"].max()) >= 4
        ),
    }
    return findings, sufficiency_summary


def _build_tables(
    frame: pd.DataFrame,
    *,
    config: MicrowireEdaConfig,
    counts: Mapping[str, int],
    applied_scope: str,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, str]]:
    tables: dict[str, pd.DataFrame] = {
        "coverage": _coverage_table(frame, columns=_report_relevant_columns(frame)),
        "row_scope": _row_scope_table(config.row_scope, applied_scope, counts, config.aggregation_mode),
        "endpoint_coverage": _endpoint_coverage_table(frame),
        "duplicate_wires": _duplicate_wire_table(frame),
        "process_strain_correlations": _target_correlation_table(frame, target="strain_abs", features=PROCESS_FEATURES),
        "process_fracture_strain_correlations": _target_correlation_table(frame, target="fracture_strain_abs", features=PROCESS_FEATURES),
        "process_stress_correlations": _target_correlation_table(frame, target="Stress (MPa)", features=PROCESS_FEATURES),
        "process_fracture_stress_correlations": _target_correlation_table(frame, target="Fracture stress (MPa)", features=PROCESS_FEATURES),
        "current_strain_correlations": _target_correlation_table(frame, target="strain_abs", features=CURRENT_FEATURES),
        "current_fracture_strain_correlations": _target_correlation_table(frame, target="fracture_strain_abs", features=CURRENT_FEATURES),
        "current_stress_correlations": _target_correlation_table(frame, target="Stress (MPa)", features=CURRENT_FEATURES),
        "current_fracture_stress_correlations": _target_correlation_table(frame, target="Fracture stress (MPa)", features=CURRENT_FEATURES),
        "geometry_strain_correlations": _target_correlation_table(frame, target="strain_abs", features=GEOMETRY_COLUMNS + DERIVED_GEOMETRY_COLUMNS),
        "geometry_fracture_strain_correlations": _target_correlation_table(frame, target="fracture_strain_abs", features=GEOMETRY_COLUMNS + DERIVED_GEOMETRY_COLUMNS),
        "geometry_stress_correlations": _target_correlation_table(frame, target="Stress (MPa)", features=GEOMETRY_COLUMNS + DERIVED_GEOMETRY_COLUMNS),
        "geometry_fracture_stress_correlations": _target_correlation_table(frame, target="Fracture stress (MPa)", features=GEOMETRY_COLUMNS + DERIVED_GEOMETRY_COLUMNS),
        "composition_strain_correlations": _target_correlation_table(frame, target="strain_abs", features=COMPOSITION_FEATURES),
        "composition_fracture_strain_correlations": _target_correlation_table(frame, target="fracture_strain_abs", features=COMPOSITION_FEATURES),
        "composition_stress_correlations": _target_correlation_table(frame, target="Stress (MPa)", features=COMPOSITION_FEATURES),
        "composition_fracture_stress_correlations": _target_correlation_table(frame, target="Fracture stress (MPa)", features=COMPOSITION_FEATURES),
        "time_summary": _time_summary_table(frame),
    }
    skipped_sections: dict[str, str] = {}
    if config.include_composition_splits:
        tables["composition_summary"] = _composition_summary_table(frame)
        tables["per_composition_process_strain_signals"] = _per_composition_signal_table(
            frame,
            target="strain_abs",
            features=PROCESS_FEATURES,
        )
        tables["per_composition_process_fracture_strain_signals"] = _per_composition_signal_table(
            frame,
            target="fracture_strain_abs",
            features=PROCESS_FEATURES,
        )
        tables["per_composition_process_stress_signals"] = _per_composition_signal_table(
            frame,
            target="Stress (MPa)",
            features=PROCESS_FEATURES,
        )
        tables["per_composition_process_fracture_stress_signals"] = _per_composition_signal_table(
            frame,
            target="Fracture stress (MPa)",
            features=PROCESS_FEATURES,
        )
        if tables["composition_summary"].empty:
            skipped_sections["cohorts"] = "Composition-split analysis is enabled, but no usable composition labels were available."
    else:
        tables["composition_summary"] = pd.DataFrame()
        tables["per_composition_process_strain_signals"] = pd.DataFrame()
        tables["per_composition_process_fracture_strain_signals"] = pd.DataFrame()
        tables["per_composition_process_stress_signals"] = pd.DataFrame()
        tables["per_composition_process_fracture_stress_signals"] = pd.DataFrame()
        skipped_sections["cohorts"] = "Composition-split analysis was disabled for this run."
    endpoint_tables = {
        "strain_abs": tables["process_strain_correlations"],
        "fracture_strain_abs": tables["process_fracture_strain_correlations"],
        "Stress (MPa)": tables["process_stress_correlations"],
        "Fracture stress (MPa)": tables["process_fracture_stress_correlations"],
    }
    if config.include_legacy_breakage_analysis:
        legacy_table = _legacy_breakage_summary(frame)
        tables["legacy_breakage_summary"] = legacy_table
        if legacy_table.empty:
            skipped_sections["legacy"] = "Legacy broke/OK comparisons were kept as auxiliary context, but there were not enough labeled rows for a stable split."
    else:
        tables["legacy_breakage_summary"] = pd.DataFrame()
        skipped_sections["legacy"] = "Legacy broke/OK analysis was disabled for this run."
    return tables, endpoint_tables, skipped_sections


def run_analysis(
    config: MicrowireEdaConfig,
    progress_callback: ProgressCallback | None = None,
) -> MicrowireEdaAnalysis:
    progress = progress_callback or _noop_progress
    progress("Loading assemble data")
    raw_frame, input_kind, working_input_path, copied_project_path, used_project_rebuild = load_analysis_frame(config, progress)
    progress("Canonicalising columns")
    canonical_frame = canonicalise_frame(raw_frame)
    scoped_frame, applied_scope = apply_row_scope(canonical_frame, config)
    progress("Applying repeated-measurement aggregation")
    scoped_frame = _aggregate_repeated_measurements(scoped_frame, config.aggregation_mode)
    scoped_frame = _ordered_export_frame(scoped_frame)
    counts = _row_counts(scoped_frame)
    progress("Preparing analysis tables")
    tables, endpoint_tables, skipped_sections = _build_tables(
        scoped_frame,
        config=config,
        counts=counts,
        applied_scope=applied_scope,
    )
    findings, sufficiency_summary = _generate_findings(
        scoped_frame,
        config=config,
        counts=counts,
        tables=tables,
        include_legacy_breakage_analysis=config.include_legacy_breakage_analysis,
        include_composition_splits=config.include_composition_splits,
        used_project_rebuild=used_project_rebuild,
    )
    return MicrowireEdaAnalysis(
        config=config,
        input_kind=input_kind,
        source_path=config.input_path,
        working_input_path=working_input_path,
        copied_project_path=copied_project_path,
        used_project_rebuild=used_project_rebuild,
        canonical_frame=canonical_frame,
        scoped_frame=scoped_frame,
        applied_scope=applied_scope,
        row_counts=counts,
        tables=tables,
        skipped_sections=skipped_sections,
        findings=findings,
        sufficiency_summary=sufficiency_summary,
        endpoint_tables=endpoint_tables,
    )


def _findings_markdown(
    *,
    title: str,
    analysis: MicrowireEdaAnalysis,
) -> str:
    lines = [
        f"# {title}",
        "",
        "## Summary",
        "",
        f"- Rows analysed: {analysis.row_counts.get('all_rows', 0)}",
        f"- Unique wires analysed: {analysis.row_counts.get('unique_wires', 0)}",
        f"- Aggregation mode: {analysis.config.aggregation_mode}",
        f"- Strain rows: {analysis.row_counts.get('numeric_strain', 0)}",
        f"- Fracture strain rows: {analysis.row_counts.get('numeric_fracture_strain', 0)}",
        f"- Stress rows: {analysis.row_counts.get('numeric_stress', 0)}",
        f"- Fracture stress rows: {analysis.row_counts.get('numeric_fracture_stress', 0)}",
    ]
    if analysis.copied_project_path is not None:
        lines.extend(
            [
                "",
                "## Working Copy",
                "",
                f"- Disposable project copy used: `{analysis.copied_project_path}`",
            ]
        )
    if analysis.used_project_rebuild:
        lines.append("- Assemble rows were rebuilt transiently from the Builder project sections for this run.")
    lines.extend(["", "## Findings", ""])
    if not analysis.findings:
        lines.append("- No findings were generated.")
    for finding in analysis.findings:
        lines.append(f"- **{finding.get('headline', 'Finding')}** {finding.get('detail', '').strip()}")
    lines.extend(
        [
            "",
            "## Cautions",
            "",
            "- These findings are observational and intended to guide follow-up experiments, not to prove causality.",
            "- Sparse endpoint coverage can change apparent rankings quickly as more measurements are added.",
            "- Current-density signals partly inherit measured diameter and should not be treated as fully independent material variables.",
            "- Repeated measurements can change rankings materially; compare raw, per-wire median, and per-wire best views before locking in a design rule.",
        ]
    )
    return "\n".join(lines) + "\n"


def _html_document(
    *,
    title: str,
    analysis: MicrowireEdaAnalysis,
    figures: Sequence[FigureArtifact],
) -> str:
    grouped_figures: dict[str, list[FigureArtifact]] = {}
    for figure in figures:
        grouped_figures.setdefault(figure.section, []).append(figure)

    findings_html = "".join(
        [
            "<article class='finding-card'>"
            f"<h3>{html.escape(str(finding.get('headline', 'Finding')))}</h3>"
            f"<p>{html.escape(str(finding.get('detail', '')))}</p>"
            "</article>"
            for finding in analysis.findings
        ]
    ) or "<p class='empty-note'>No findings were generated.</p>"

    sections = [
        (
            "quality",
            "Data quality",
            "Coverage, endpoint availability, duplicate-wire checks, and cohort sizes.",
            [
                _table_html(analysis.tables["coverage"]),
                _table_html(analysis.tables["row_scope"]),
                _table_html(analysis.tables["endpoint_coverage"]),
                _table_html(analysis.tables["duplicate_wires"]),
            ],
        ),
        (
            "overview",
            "Endpoint overview",
            "Distributions for strain, fracture strain, stress, and fracture stress measured in the assembled dataset.",
            [],
        ),
        (
            "process",
            "Process to outcome",
            "Process-side correlations to the measured mechanical endpoints.",
            [
                _table_html(analysis.tables["process_strain_correlations"]),
                _table_html(analysis.tables["process_fracture_strain_correlations"]),
                _table_html(analysis.tables["process_stress_correlations"]),
                _table_html(analysis.tables["process_fracture_stress_correlations"]),
            ],
        ),
        (
            "current",
            "Current to outcome",
            "Current and current-density correlations, reported separately because some current-density terms are derived from measured diameter.",
            [
                _table_html(analysis.tables["current_strain_correlations"]),
                _table_html(analysis.tables["current_fracture_strain_correlations"]),
                _table_html(analysis.tables["current_stress_correlations"]),
                _table_html(analysis.tables["current_fracture_stress_correlations"]),
            ],
        ),
        (
            "geometry",
            "Geometry to outcome",
            "Geometry-side correlations and scatter views for d, D, and d/D.",
            [
                _table_html(analysis.tables["geometry_strain_correlations"]),
                _table_html(analysis.tables["geometry_fracture_strain_correlations"]),
                _table_html(analysis.tables["geometry_stress_correlations"]),
                _table_html(analysis.tables["geometry_fracture_stress_correlations"]),
            ],
        ),
        (
            "composition-signals",
            "Composition to outcome",
            "Parsed elemental-content correlations that complement the nominal composition labels. Treat these cautiously because composition features are not independent.",
            [
                _table_html(analysis.tables["composition_strain_correlations"]),
                _table_html(analysis.tables["composition_fracture_strain_correlations"]),
                _table_html(analysis.tables["composition_stress_correlations"]),
                _table_html(analysis.tables["composition_fracture_stress_correlations"]),
            ],
        ),
        (
            "cohorts",
            "Cohort splits",
            "Cross-composition and per-composition coverage summaries to show where the dataset is concentrated.",
            [
                _table_html(analysis.tables["composition_summary"]),
                _table_html(analysis.tables["per_composition_process_strain_signals"]),
                _table_html(analysis.tables["per_composition_process_fracture_strain_signals"]),
                _table_html(analysis.tables["per_composition_process_stress_signals"]),
                _table_html(analysis.tables["per_composition_process_fracture_stress_signals"]),
            ],
        ),
        (
            "interactions",
            "Interaction views",
            "Multivariate views to spot clusters, regimes, and missing-data constraints.",
            [],
        ),
        (
            "time",
            "Time drift",
            "Month-by-month coverage and production-date trends.",
            [
                _table_html(analysis.tables["time_summary"]),
            ],
        ),
        (
            "findings",
            "Findings",
            "Auto-generated observations that summarize the strongest current signals and the main limitations.",
            [findings_html],
        ),
    ]

    if analysis.config.include_legacy_breakage_analysis:
        sections.insert(
            6,
            (
                "legacy",
                "Legacy breakage context",
                "Auxiliary broke/OK comparisons kept for backward compatibility with older EDA framing.",
                [f"<p class='skip-note'>{html.escape(analysis.skipped_sections.get('legacy', ''))}</p>", _table_html(analysis.tables["legacy_breakage_summary"])],
            ),
        )

    parts: list[str] = []
    for section_key, heading, intro, content_html in sections:
        parts.append(f"<section><h2>{html.escape(heading)}</h2><p>{html.escape(intro)}</p>")
        if section_key in analysis.skipped_sections and section_key != "legacy":
            parts.append(f"<p class='skip-note'>{html.escape(analysis.skipped_sections[section_key])}</p>")
        for figure in grouped_figures.get(section_key, []):
            parts.append(f"<article class='figure-card'><h3>{html.escape(figure.title)}</h3>{figure.html}</article>")
        parts.extend(content_html)
        parts.append("</section>")

    copy_note = ""
    if analysis.copied_project_path is not None:
        copy_note = (
            "<p class='lead'><strong>Copy-safe run:</strong> this analysis used a disposable project copy at "
            f"{html.escape(str(analysis.copied_project_path))}.</p>"
        )
    if analysis.used_project_rebuild:
        copy_note += "<p class='lead'><strong>Transient rebuild:</strong> Assemble rows were rebuilt in-process from the Builder project sections for this run.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{
      font-family: "Segoe UI", Arial, sans-serif;
      margin: 24px auto;
      max-width: 1380px;
      color: #1f2933;
      background: linear-gradient(180deg, #f5f7fb 0%, #ffffff 18%);
      padding: 0 20px 40px;
    }}
    h1, h2, h3 {{ color: #17324d; }}
    .lead {{ color: #52606d; margin-bottom: 18px; }}
    section {{
      background: #ffffff;
      border: 1px solid #d9e2ec;
      border-radius: 14px;
      padding: 18px 20px 22px;
      margin: 18px 0;
      box-shadow: 0 10px 22px rgba(15, 23, 42, 0.04);
    }}
    .figure-card, .finding-card {{
      margin: 16px 0 22px;
      padding: 14px;
      border-radius: 12px;
      background: #fbfdff;
      border: 1px solid #e6eef7;
    }}
    .figure-card img {{ width: 100%; height: auto; display: block; border-radius: 10px; background: white; }}
    .eda-table {{ width: 100%; border-collapse: collapse; margin: 10px 0 18px; font-size: 14px; }}
    .eda-table th, .eda-table td {{ border: 1px solid #d9e2ec; padding: 6px 8px; text-align: left; vertical-align: top; }}
    .eda-table th {{ background: #f0f4f8; }}
    .skip-note {{ padding: 10px 12px; border-left: 4px solid #f0ad4e; background: #fff8e7; border-radius: 8px; }}
    .empty-note {{ color: #52606d; font-style: italic; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="lead">Analysis-first exploratory report for microwire assemble data. Modern strain and fracture endpoints are primary; legacy broke/OK labels remain auxiliary context only. Aggregation mode: {html.escape(analysis.config.aggregation_mode)}.</p>
  {copy_note}
  {''.join(parts)}
</body>
</html>"""


def _write_summary_workbook(
    *,
    output_path: Path,
    analysis: MicrowireEdaAnalysis,
) -> None:
    with pd.ExcelWriter(output_path) as writer:
        analysis.scoped_frame.to_excel(writer, sheet_name="dataset", index=False)
        for sheet_name in SUMMARY_TABLE_ORDER:
            table = analysis.tables.get(sheet_name)
            if isinstance(table, pd.DataFrame):
                table.to_excel(writer, sheet_name=_slugify(sheet_name)[:31], index=False)


def _write_findings_json(
    *,
    path: Path,
    analysis: MicrowireEdaAnalysis,
) -> None:
    payload = {
        "kind": "MicrowireEDAFindings",
        "version": 1,
        "source_path": str(analysis.source_path) if analysis.source_path is not None else None,
        "working_input_path": str(analysis.working_input_path) if analysis.working_input_path is not None else None,
        "copied_project_path": str(analysis.copied_project_path) if analysis.copied_project_path is not None else None,
        "used_project_rebuild": bool(analysis.used_project_rebuild),
        "aggregation_mode": analysis.config.aggregation_mode,
        "row_counts": dict(analysis.row_counts),
        "sufficiency_summary": dict(analysis.sufficiency_summary),
        "findings": analysis.findings,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_manifest(
    *,
    path: Path,
    analysis: MicrowireEdaAnalysis,
    report_path: Path,
    workbook_path: Path,
    csv_path: Path,
    pdf_path: Path | None,
    figure_artifacts: Sequence[FigureArtifact],
    findings_json_path: Path | None,
    findings_md_path: Path | None,
) -> None:
    payload = {
        "kind": "MicrowireEDA",
        "version": 2,
        "input_path": str(analysis.source_path) if analysis.source_path is not None else None,
        "working_input_path": str(analysis.working_input_path) if analysis.working_input_path is not None else None,
        "copied_project_path": str(analysis.copied_project_path) if analysis.copied_project_path is not None else None,
        "used_project_rebuild": bool(analysis.used_project_rebuild),
        "input_kind": analysis.input_kind,
        "row_scope_requested": analysis.config.row_scope,
        "row_scope_applied": analysis.applied_scope,
        "aggregation_mode": analysis.config.aggregation_mode,
        "report_title": analysis.config.report_title,
        "output_dir": str(path.parent),
        "report_path": str(report_path),
        "workbook_path": str(workbook_path),
        "csv_path": str(csv_path),
        "pdf_path": str(pdf_path) if pdf_path else None,
        "findings_json_path": str(findings_json_path) if findings_json_path else None,
        "findings_md_path": str(findings_md_path) if findings_md_path else None,
        "row_counts": dict(analysis.row_counts),
        "sufficiency_summary": dict(analysis.sufficiency_summary),
        "tables": {
            str(name): {"rows": int(table.shape[0]), "columns": int(table.shape[1])}
            for name, table in analysis.tables.items()
        },
        "figures": [
            {
                "key": figure.key,
                "title": figure.title,
                "section": figure.section,
                "png_path": str(figure.png_path) if figure.png_path is not None else None,
            }
            for figure in figure_artifacts
        ],
        "skipped_sections": dict(analysis.skipped_sections),
        "finding_count": len(analysis.findings),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_analysis_artifacts(
    analysis: MicrowireEdaAnalysis,
    progress_callback: ProgressCallback | None = None,
) -> MicrowireEdaResult:
    progress = progress_callback or _noop_progress
    output_dir = _normalise_output_dir(analysis.config).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    report_path = output_dir / "microwire_eda_report.html"
    workbook_path = output_dir / "microwire_eda_summary.xlsx"
    csv_path = output_dir / "microwire_eda_dataset.csv"
    manifest_path = output_dir / "microwire_eda_manifest.json"
    findings_json_path = output_dir / DEFAULT_FINDINGS_FILENAME if analysis.config.write_findings else None
    findings_md_path = output_dir / DEFAULT_FINDINGS_MD_FILENAME if analysis.config.write_findings else None
    pdf_path = output_dir / "microwire_eda_figures.pdf" if analysis.config.export_pdf_bundle else None

    progress("Building figures")
    figure_specs: list[tuple[str, str, str, plt.Figure]] = [
        ("quality", "coverage_heatmap", "Coverage heatmap", _coverage_figure(analysis.scoped_frame)),
        ("overview", "endpoint_overview", "Endpoint overview", _endpoint_overview_figure(analysis.scoped_frame)),
        (
            "process",
            "process_strain_correlation",
            "Top process correlations with |Strain|",
            _correlation_bar_figure(analysis.tables["process_strain_correlations"], title="Top process correlations with |Strain|"),
        ),
        (
            "process",
            "process_fracture_stress_correlation",
            "Top process correlations with fracture stress",
            _correlation_bar_figure(analysis.tables["process_fracture_stress_correlations"], title="Top process correlations with fracture stress"),
        ),
        (
            "current",
            "current_strain_correlation",
            "Top current correlations with |Strain|",
            _correlation_bar_figure(analysis.tables["current_strain_correlations"], title="Top current correlations with |Strain|"),
        ),
        (
            "geometry",
            "geometry_strain_scatter",
            "Geometry vs |Strain|",
            _scatter_grid(
                analysis.scoped_frame,
                target="strain_abs",
                features=[feature for feature in GEOMETRY_COLUMNS if feature in analysis.scoped_frame.columns],
                title="Geometry vs |Strain|",
            ),
        ),
        (
            "geometry",
            "geometry_fracture_strain_scatter",
            "Geometry vs |Fracture strain|",
            _scatter_grid(
                analysis.scoped_frame,
                target="fracture_strain_abs",
                features=[feature for feature in GEOMETRY_COLUMNS if feature in analysis.scoped_frame.columns],
                title="Geometry vs |Fracture strain|",
            ),
        ),
        ("interactions", "pairplot", "Pairplot", _pairplot_figure(analysis.scoped_frame)),
        ("interactions", "parallel_coordinates", "Parallel coordinates", _parallel_coordinates_figure(analysis.scoped_frame)),
        ("time", "time_drift", "Time drift", _time_drift_figure(analysis.scoped_frame)),
    ]
    if analysis.config.include_composition_splits:
        figure_specs.insert(2, ("cohorts", "composition_coverage", "Composition coverage", _composition_coverage_figure(analysis.scoped_frame)))
    figure_specs.insert(
        5,
        (
            "composition-signals",
            "composition_fracture_stress_correlation",
            "Top composition correlations with fracture stress",
            _correlation_bar_figure(
                analysis.tables["composition_fracture_stress_correlations"],
                title="Top composition correlations with fracture stress",
            ),
        ),
    )
    if analysis.config.include_legacy_breakage_analysis:
        figure_specs.append(
            (
                "legacy",
                "legacy_breakage_correlation",
                "Legacy broke/OK summary",
                _correlation_bar_figure(
                    analysis.tables["legacy_breakage_summary"].rename(columns={"delta_broke_minus_ok": "rho", "feature": "feature"}),
                    title="Legacy broke/OK delta summary",
                )
                if not analysis.tables["legacy_breakage_summary"].empty
                else _blank_figure("Legacy broke/OK summary", "Not enough labeled rows for a stable broke/OK comparison."),
            )
        )

    pdf = PdfPages(pdf_path) if pdf_path is not None else None
    figure_artifacts: list[FigureArtifact] = []
    try:
        for section, key, title, figure in figure_specs:
            figure_artifacts.append(
                _save_figure(
                    figure,
                    output_dir=figures_dir,
                    key=key,
                    title=title,
                    section=section,
                    pdf=pdf,
                    write_png=analysis.config.export_png_bundle,
                )
            )
    finally:
        if pdf is not None:
            pdf.close()

    progress("Writing report bundle")
    analysis.scoped_frame.to_csv(csv_path, index=False)
    _write_summary_workbook(output_path=workbook_path, analysis=analysis)
    report_path.write_text(_html_document(title=analysis.config.report_title, analysis=analysis, figures=figure_artifacts), encoding="utf-8")

    if findings_json_path is not None:
        _write_findings_json(path=findings_json_path, analysis=analysis)
    if findings_md_path is not None:
        findings_md_path.write_text(_findings_markdown(title=analysis.config.report_title, analysis=analysis), encoding="utf-8")

    _write_manifest(
        path=manifest_path,
        analysis=analysis,
        report_path=report_path,
        workbook_path=workbook_path,
        csv_path=csv_path,
        pdf_path=pdf_path if pdf_path and pdf_path.exists() else None,
        figure_artifacts=figure_artifacts,
        findings_json_path=findings_json_path if findings_json_path and findings_json_path.exists() else None,
        findings_md_path=findings_md_path if findings_md_path and findings_md_path.exists() else None,
    )

    return MicrowireEdaResult(
        config=analysis.config,
        input_kind=analysis.input_kind,
        output_dir=output_dir,
        report_path=report_path,
        workbook_path=workbook_path,
        csv_path=csv_path,
        manifest_path=manifest_path,
        pdf_path=pdf_path if pdf_path and pdf_path.exists() else None,
        figure_paths=[artifact.png_path for artifact in figure_artifacts if artifact.png_path is not None],
        skipped_sections=analysis.skipped_sections,
        row_counts=dict(analysis.row_counts),
        tables=analysis.tables,
        findings_json_path=findings_json_path if findings_json_path and findings_json_path.exists() else None,
        findings_md_path=findings_md_path if findings_md_path and findings_md_path.exists() else None,
        copied_project_path=analysis.copied_project_path,
        used_project_rebuild=analysis.used_project_rebuild,
        sufficiency_summary=dict(analysis.sufficiency_summary),
        endpoint_tables=analysis.endpoint_tables,
        findings=list(analysis.findings),
    )


def generate_report(
    config: MicrowireEdaConfig,
    progress_callback: ProgressCallback | None = None,
) -> MicrowireEdaResult:
    progress = progress_callback or _noop_progress
    analysis = run_analysis(config, progress_callback=progress)
    progress("Writing output files")
    result = write_analysis_artifacts(analysis, progress_callback=progress)
    progress("Finished")
    return result
