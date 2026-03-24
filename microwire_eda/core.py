from __future__ import annotations

import base64
import html
import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, mean_squared_error, r2_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

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

STATUS_COLORS = {
    STATUS_OK: "#2e8b57",
    STATUS_BROKE: "#d94f4f",
    STATUS_NO_DATA: "#9c9c9c",
}

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
    "As current density (A/mm^2)",
    "Ms current density (A/mm^2)",
    "Low mA value (mA)",
]
FUNCTIONAL_COLUMNS = [
    "Stress (MPa)",
    "Fracture stress (MPa)",
    "Strain (%)",
    "Fracture strain (%)",
    "Legacy strain",
]
OUTCOME_COLUMNS = ["is_broken", "strain_abs", "fracture_strain_abs"]

CANONICAL_COLUMNS = (
    ["Composition", "Microwire", "Production datetime"]
    + GEOMETRY_COLUMNS
    + FABRICATION_COLUMNS
    + ANNEALING_COLUMNS
    + FUNCTIONAL_COLUMNS
    + OUTCOME_COLUMNS
)

CONTROL_FEATURES = [
    "d (µm)",
    "D (µm)",
    "d/D",
    "Core temperature (°C)",
    "Glass temperature (°C)",
    "Winding speed (m/min)",
    "Glass feeding (mm/min)",
    "Underpressure",
    "Length (m)",
    "Mass (g)",
    "e/a",
    "As current density (A/mm^2)",
    "Ms current density (A/mm^2)",
    "Low mA value (mA)",
    "As1 (mA)",
    "Af1 (mA)",
    "Ms1 (mA)",
    "Mf1 (mA)",
]

SUMMARY_TABLE_ORDER = [
    "coverage",
    "row_scope",
    "spearman_all",
    "strain_correlations",
    "fracture_strain_correlations",
    "fracture_stress_correlations",
    "sweet_spots",
    "model_b_metrics",
    "model_c_metrics",
    "model_d_metrics",
]

HEADER_ALIASES = {
    "temperature (°c)": "Core temperature (°C)",
    "core temperature (°c)": "Core temperature (°C)",
    "glass temperature (°c)": "Glass temperature (°C)",
    "d (µm)": "d (µm)",
    "d (âµm)": "d (µm)",
    "d (μm)": "d (µm)",
    "d (î¼m)": "d (µm)",
    "d (痠)": "d (µm)",
    "d [µm]": "d (µm)",
    "d [um]": "d (µm)",
    "d_um": "d (µm)",
    "D (µm)": "D (µm)",
    "D (âµm)": "D (µm)",
    "D (μm)": "D (µm)",
    "D (î¼m)": "D (µm)",
    "D (痠)": "D (µm)",
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
    "fracture stress (mpa)": "Fracture stress (MPa)",
    "brittle": "Brittle",
    "broken": "is_broken",
    "is broken": "is_broken",
    "production datetime": "Production datetime",
}


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


@dataclass(slots=True)
class FigureArtifact:
    key: str
    title: str
    section: str
    png_path: Path | None
    html: str


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


def _slugify(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = re.sub(r"[^0-9A-Za-z._-]+", "-", text).strip("-._")
    return text or "artifact"


def _header_key(value: object) -> str:
    return str(value).strip().replace("\ufeff", "").casefold()


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
    return pd.to_datetime(series, errors="coerce")


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


def _load_project_frame(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sections = payload.get("sections", {})
    assemble = sections.get("assemble", {}) if isinstance(sections, Mapping) else {}
    rows = assemble.get("rows", []) if isinstance(assemble, Mapping) else []
    if not isinstance(rows, list):
        raise ValueError("Project file is missing assemble rows.")
    return pd.DataFrame(rows)


def _load_excel_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path)


def load_input_frame(config: MicrowireEdaConfig) -> tuple[pd.DataFrame, str]:
    if isinstance(config.source_dataframe, pd.DataFrame):
        return _copy_frame(config.source_dataframe), INPUT_KIND_DATAFRAME
    if config.input_path is None:
        raise ValueError("Microwire EDA requires an input path or source dataframe.")
    kind = detect_input_kind(config.input_path, config.input_kind)
    if kind == INPUT_KIND_PROJECT:
        return _load_project_frame(config.input_path), kind
    if kind == INPUT_KIND_EXCEL:
        return _load_excel_frame(config.input_path), kind
    raise ValueError(f"Unsupported input kind: {kind}")


def _canonical_column_names(columns: Iterable[object]) -> list[str]:
    output: list[str] = []
    for column in columns:
        raw = str(column).strip().replace("\ufeff", "")
        key = HEADER_ALIASES.get(_header_key(raw), raw)
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

    if "Legacy strain" not in clean.columns:
        source = _first_present(clean, ["Legacy strain", "Strain"])
        if source is not None:
            clean["Legacy strain"] = source

    if "Production datetime" in clean.columns:
        clean["Production datetime"] = _coerce_datetime(clean["Production datetime"])

    numeric_candidates = set(GEOMETRY_COLUMNS + FABRICATION_COLUMNS + ANNEALING_COLUMNS + FUNCTIONAL_COLUMNS)
    for column in numeric_candidates:
        if column in clean.columns:
            clean[column] = clean[column].map(_parse_numeric)

    for column in ["Brittle", "is_broken"]:
        if column in clean.columns:
            clean[column] = clean[column].map(_parse_boolish)

    explicit_broken = _first_present(clean, ["is_broken", "Brittle"])
    strain_series = _first_present(clean, ["Strain (%)"])
    legacy_strain_series = _first_present(clean, ["Legacy strain", "Strain"])
    fracture_strain_series = _first_present(clean, ["Fracture strain (%)"])

    derived_broken: list[int | float] = []
    for idx in range(len(clean.index)):
        broken_value = None
        if explicit_broken is not None:
            broken_value = _parse_boolish(explicit_broken.iloc[idx])
        strain_value = _parse_numeric(strain_series.iloc[idx]) if strain_series is not None else math.nan
        if math.isnan(strain_value) and legacy_strain_series is not None:
            strain_value = _parse_numeric(legacy_strain_series.iloc[idx])
        fracture_value = _parse_numeric(fracture_strain_series.iloc[idx]) if fracture_strain_series is not None else math.nan
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
    elif legacy_strain_series is not None:
        clean["strain_abs"] = legacy_strain_series.map(lambda value: abs(_parse_numeric(value)))
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


def _table_html(frame: pd.DataFrame, *, index: bool = False) -> str:
    return frame.copy().to_html(index=index, border=0, classes="eda-table")


def _figure_to_html(fig: plt.Figure) -> str:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=180, bbox_inches="tight")
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


def _normalise_output_dir(config: MicrowireEdaConfig) -> Path:
    if isinstance(config.output_dir, Path):
        return config.output_dir / _slugify(config.report_title)
    if isinstance(config.input_path, Path):
        return config.input_path.with_suffix("").parent / f"{_slugify(config.report_title)}_report"
    return Path.cwd() / f"{_slugify(config.report_title)}_report"


def _ordered_export_frame(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = [column for column in CANONICAL_COLUMNS if column in frame.columns]
    remaining = [column for column in frame.columns if column not in ordered]
    return frame.loc[:, ordered + remaining].copy()


def _complete_case_count(frame: pd.DataFrame, columns: Sequence[str]) -> int:
    usable = [column for column in columns if column in frame.columns]
    if not usable:
        return 0
    return int(frame[usable].notna().all(axis=1).sum())


def _usable_row_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {
        "all_rows": int(len(frame.index)),
        "known_outcome": int(frame.get("is_broken", pd.Series(dtype=float)).notna().sum()),
        "numeric_strain": int(frame.get("strain_abs", pd.Series(dtype=float)).notna().sum()),
        "numeric_fracture_strain": int(
            frame.get("fracture_strain_abs", pd.Series(dtype=float)).notna().sum()
        ),
        "geometry_plus_strain": _complete_case_count(frame, ["d (µm)", "D (µm)", "d/D", "strain_abs"]),
        "geometry_plus_fracture_strain": _complete_case_count(
            frame,
            ["d (µm)", "D (µm)", "d/D", "fracture_strain_abs"],
        ),
        "core_controls_plus_strain": _complete_case_count(
            frame,
            [
                "d (µm)",
                "D (µm)",
                "d/D",
                "Core temperature (°C)",
                "Winding speed (m/min)",
                "Glass feeding (mm/min)",
                "Underpressure",
                "strain_abs",
            ],
        ),
    }


def _coverage_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    total = max(len(frame.index), 1)
    for column in _ordered_columns(frame):
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


def _row_scope_table(requested: str, applied: str, counts: Mapping[str, int]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"metric": "requested_scope", "value": requested},
            {"metric": "applied_scope", "value": applied},
            {"metric": "rows", "value": counts.get("all_rows", 0)},
            {"metric": "known_outcome", "value": counts.get("known_outcome", 0)},
            {"metric": "numeric_strain", "value": counts.get("numeric_strain", 0)},
            {
                "metric": "numeric_fracture_strain",
                "value": counts.get("numeric_fracture_strain", 0),
            },
        ]
    )


def _safe_spearman(frame: pd.DataFrame, left: str, right: str) -> tuple[float, float, int]:
    subset = frame[[left, right]].dropna()
    if len(subset.index) < 3:
        return math.nan, math.nan, int(len(subset.index))
    if int(subset[left].nunique()) < 2 or int(subset[right].nunique()) < 2:
        return math.nan, math.nan, int(len(subset.index))
    rho, p_value = stats.spearmanr(subset[left], subset[right])
    return float(rho), float(p_value), int(len(subset.index))


def _spearman_table(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    usable = [column for column in columns if column in frame.columns]
    rows: list[dict[str, Any]] = []
    for idx, left in enumerate(usable):
        for right in usable[idx + 1 :]:
            rho, p_value, n = _safe_spearman(frame, left, right)
            rows.append(
                {
                    "column_a": left,
                    "column_b": right,
                    "rho": rho,
                    "p_value": p_value,
                    "n": n,
                }
            )
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["column_a", "column_b"]).reset_index(drop=True)
    return result


def _target_correlation_table(frame: pd.DataFrame, target: str) -> pd.DataFrame:
    if target not in frame.columns:
        return pd.DataFrame(columns=["feature", "rho", "p_value", "n"])
    rows: list[dict[str, Any]] = []
    for feature in CONTROL_FEATURES + ["Stress (MPa)", "Fracture stress (MPa)"]:
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
        result = result.sort_values("abs_rho", ascending=False).reset_index(drop=True)
    return result


def _broke_ok_summary(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.loc[frame["is_broken"].isin([0, 1])].copy()
    rows: list[dict[str, Any]] = []
    if working.empty:
        return pd.DataFrame(rows)
    for feature in CONTROL_FEATURES + ["Stress (MPa)"]:
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


def _sweet_spot_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    targets = [target for target in ["strain_abs", "fracture_strain_abs", "Fracture stress (MPa)"] if target in frame.columns]
    if not targets:
        return pd.DataFrame(rows)
    for feature in CONTROL_FEATURES:
        if feature not in frame.columns:
            continue
        subset = frame[[feature] + targets].dropna(how="all")
        if len(subset.index) < 6:
            continue
        try:
            subset = subset.assign(bucket=pd.qcut(subset[feature], q=3, duplicates="drop"))
        except Exception:
            continue
        for bucket, bucket_frame in subset.groupby("bucket", observed=False):
            if len(bucket_frame.index) < 2:
                continue
            rows.append(
                {
                    "feature": feature,
                    "bucket": str(bucket),
                    "mean_strain_abs": float(bucket_frame["strain_abs"].dropna().mean()) if "strain_abs" in bucket_frame else math.nan,
                    "mean_fracture_strain_abs": float(bucket_frame["fracture_strain_abs"].dropna().mean()) if "fracture_strain_abs" in bucket_frame else math.nan,
                    "mean_fracture_stress_mpa": float(bucket_frame["Fracture stress (MPa)"].dropna().mean()) if "Fracture stress (MPa)" in bucket_frame else math.nan,
                    "n": int(len(bucket_frame.index)),
                }
            )
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["feature", "bucket"]).reset_index(drop=True)
    return result


def _status_series(frame: pd.DataFrame) -> pd.Series:
    status = pd.Series(STATUS_NO_DATA, index=frame.index, dtype=object)
    if "is_broken" not in frame.columns:
        return status
    status.loc[frame["is_broken"].eq(0)] = STATUS_OK
    status.loc[frame["is_broken"].eq(1)] = STATUS_BROKE
    return status


def _coverage_figure(frame: pd.DataFrame) -> plt.Figure:
    columns = [column for column in CANONICAL_COLUMNS if column in frame.columns]
    if not columns:
        return _blank_figure("Coverage report", "No canonical columns are available.")
    coverage_frame = frame.loc[:, columns].copy().head(120)
    matrix = coverage_frame.notna().astype(int).T.values
    fig, ax = plt.subplots(figsize=(max(10, len(coverage_frame.index) * 0.16), max(6, len(columns) * 0.28)))
    image = ax.imshow(matrix, aspect="auto", cmap=ListedColormap(["#d32f2f", "#43a047"]), interpolation="nearest", vmin=0, vmax=1)
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


def _outcome_overview_figure(frame: pd.DataFrame) -> plt.Figure:
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    axes = axes.ravel()
    for axis, column, title, color in [
        (axes[0], "strain_abs", "|Strain| distribution", "#4f8bc9"),
        (axes[1], "fracture_strain_abs", "|Fracture strain| distribution", "#e39d34"),
        (axes[2], "Stress (MPa)", "Stress distribution", "#8f63c8"),
        (axes[3], "Fracture stress (MPa)", "Fracture stress distribution", "#26a69a"),
    ]:
        series = frame.get(column, pd.Series(dtype=float)).dropna()
        if series.empty:
            axis.text(0.5, 0.5, "No data", ha="center", va="center")
            axis.set_axis_off()
            continue
        axis.hist(series, bins=min(10, max(4, len(series))), color=color, edgecolor="white")
        axis.set_title(title)
    fig.tight_layout()
    return fig


def _distribution_grid_figure(frame: pd.DataFrame, columns: Sequence[str], title: str) -> plt.Figure:
    available = [column for column in columns if column in frame.columns and frame[column].notna().any()]
    if not available:
        return _blank_figure(title, "No numeric variables are available for this section.")
    rows = math.ceil(len(available) / 2)
    fig, axes = plt.subplots(rows, 2, figsize=(12, max(4, rows * 3.4)))
    axes_array = np.atleast_1d(axes).ravel()
    for ax, column in zip(axes_array, available):
        values = frame[column].dropna()
        ax.hist(values, bins=min(10, max(4, values.nunique())), color="#5b9bd5", edgecolor="white")
        ax.axvline(values.median(), color="#d9534f", linestyle="--", linewidth=1.2)
        ax.set_title(column)
    for ax in axes_array[len(available):]:
        ax.axis("off")
    fig.suptitle(title)
    fig.tight_layout()
    return fig


def _annotated_heatmap(corr: pd.DataFrame, *, title: str, cmap: str = "coolwarm") -> plt.Figure:
    if corr.empty:
        return _blank_figure(title, "Not enough numeric data for a correlation heatmap.")
    fig, ax = plt.subplots(figsize=(max(7, len(corr.columns) * 1.2), max(5, len(corr.index) * 0.8)))
    image = ax.imshow(corr.values, cmap=cmap, vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(corr.columns)))
    ax.set_xticklabels(corr.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(corr.index)))
    ax.set_yticklabels(corr.index)
    for row in range(corr.shape[0]):
        for col in range(corr.shape[1]):
            value = corr.iat[row, col]
            if math.isnan(float(value)):
                continue
            ax.text(col, row, f"{value:.2f}", ha="center", va="center", fontsize=8)
    fig.colorbar(image, ax=ax)
    ax.set_title(title)
    fig.tight_layout()
    return fig


def _correlation_matrix(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    available = [column for column in columns if column in frame.columns and frame[column].notna().sum() >= 2]
    if len(available) < 2:
        return pd.DataFrame()
    numeric = frame.loc[:, available].apply(pd.to_numeric, errors="coerce")
    return numeric.corr(method="spearman")


def _scatter_grid(frame: pd.DataFrame, *, target: str, title: str) -> plt.Figure:
    table = _target_correlation_table(frame, target)
    features = table["feature"].head(6).tolist() if not table.empty else [column for column in CONTROL_FEATURES if column in frame.columns][:6]
    if target not in frame.columns or not features:
        return _blank_figure(title, "Not enough data for scatter plots.")
    rows = math.ceil(len(features) / 2)
    fig, axes = plt.subplots(rows, 2, figsize=(12, max(4, rows * 3.4)))
    axes_array = np.atleast_1d(axes).ravel()
    status = _status_series(frame)
    color_values = status.map(STATUS_COLORS).fillna("#9c9c9c")
    target_series = pd.to_numeric(frame[target], errors="coerce")
    for ax, feature in zip(axes_array, features):
        feature_series = pd.to_numeric(frame.get(feature), errors="coerce")
        mask = feature_series.notna() & target_series.notna()
        if int(mask.sum()) < 2:
            ax.axis("off")
            continue
        ax.scatter(feature_series[mask], target_series[mask], c=color_values[mask], alpha=0.85, edgecolors="white", linewidths=0.5)
        try:
            slope, intercept = np.polyfit(feature_series[mask], target_series[mask], 1)
            xs = np.linspace(float(feature_series[mask].min()), float(feature_series[mask].max()), 100)
            ax.plot(xs, slope * xs + intercept, color="#8e5ea2", linestyle="--", linewidth=1)
        except Exception:
            pass
        ax.set_title(feature)
        ax.set_xlabel(feature)
        ax.set_ylabel(target)
    for ax in axes_array[len(features):]:
        ax.axis("off")
    fig.suptitle(title)
    fig.tight_layout()
    return fig


def _geometry_to_function_figure(frame: pd.DataFrame) -> plt.Figure:
    targets = [("strain_abs", "|Strain|"), ("fracture_strain_abs", "|Fracture strain|")]
    fig, axes = plt.subplots(2, 3, figsize=(14, 8))
    status = _status_series(frame)
    colors = status.map(STATUS_COLORS).fillna("#9c9c9c")
    for row_idx, (target, label) in enumerate(targets):
        target_series = pd.to_numeric(frame.get(target), errors="coerce")
        for col_idx, feature in enumerate(GEOMETRY_COLUMNS):
            ax = axes[row_idx, col_idx]
            if feature not in frame.columns:
                ax.axis("off")
                continue
            feature_series = pd.to_numeric(frame[feature], errors="coerce")
            mask = feature_series.notna() & target_series.notna()
            if int(mask.sum()) < 2:
                ax.text(0.5, 0.5, "Not enough data", ha="center", va="center")
                ax.set_axis_off()
                continue
            ax.scatter(feature_series[mask], target_series[mask], c=colors[mask], alpha=0.85, edgecolors="white", linewidths=0.5)
            ax.set_title(f"{feature} vs {label}")
            ax.set_xlabel(feature)
            ax.set_ylabel(label)
    fig.tight_layout()
    return fig


def _pairplot_figure(frame: pd.DataFrame) -> plt.Figure:
    columns = [column for column in ["e/a", "d (µm)", "D (µm)", "d/D", "Length (m)"] if column in frame.columns and int(frame[column].notna().sum()) >= 6]
    if len(columns) < 3:
        return _blank_figure("Pairplot", "Not enough overlapping numeric rows for a pairplot.")
    working = frame[columns + ["is_broken"]].dropna(subset=columns, how="all").copy()
    if len(working.index) < 6:
        return _blank_figure("Pairplot", "Not enough overlapping numeric rows for a pairplot.")
    axis_array = scatter_matrix(
        working[columns],
        figsize=(3 * len(columns), 3 * len(columns)),
        diagonal="hist",
        alpha=0.65,
        color="#5b9bd5",
    )
    fig = axis_array[0, 0].figure
    fig.suptitle("Pairplot of top numeric columns", fontsize=14)
    fig.tight_layout()
    return fig


def _interaction_grid_figure(frame: pd.DataFrame) -> plt.Figure:
    x_columns = [column for column in ["Core temperature (°C)", "Winding speed (m/min)", "Glass feeding (mm/min)", "Underpressure"] if column in frame.columns and int(frame[column].notna().sum()) >= 3]
    y_columns = [column for column in GEOMETRY_COLUMNS if column in frame.columns and int(frame[column].notna().sum()) >= 3]
    if not x_columns or not y_columns:
        return _blank_figure("Interaction grid", "Not enough controllable and geometry rows for the interaction grid.")
    fig, axes = plt.subplots(len(y_columns), len(x_columns), figsize=(4 * len(x_columns), 3.5 * len(y_columns)))
    axes_grid = np.atleast_2d(axes)
    strain_values = frame["strain_abs"].dropna()
    vmin = float(strain_values.min()) if not strain_values.empty else 0.0
    vmax = float(strain_values.max()) if not strain_values.empty else 1.0
    for row_idx, y_col in enumerate(y_columns):
        for col_idx, x_col in enumerate(x_columns):
            ax = axes_grid[row_idx, col_idx]
            subset = frame[[x_col, y_col, "strain_abs"]].dropna(subset=[x_col, y_col])
            if subset.empty:
                ax.axis("off")
                continue
            ax.scatter(
                subset[x_col],
                subset[y_col],
                c=subset["strain_abs"].fillna(0.0),
                cmap="RdYlGn",
                vmin=vmin,
                vmax=vmax,
                alpha=0.85,
            )
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)
    fig.tight_layout()
    return fig


def _parallel_coordinates_figure(frame: pd.DataFrame) -> plt.Figure:
    columns = [column for column in GEOMETRY_COLUMNS + ["Core temperature (°C)", "Winding speed (m/min)", "Glass feeding (mm/min)", "Underpressure"] if column in frame.columns]
    if len(columns) < 4:
        return _blank_figure("Parallel coordinates", "Not enough assembled numeric columns for parallel coordinates.")
    working = frame.loc[:, columns + ["fracture_strain_abs"]].dropna(subset=columns, how="any")
    if len(working.index) < 6:
        return _blank_figure("Parallel coordinates", "Not enough complete assembled rows for parallel coordinates.")
    normalized = working.copy()
    for column in columns:
        minimum = float(normalized[column].min())
        maximum = float(normalized[column].max())
        normalized[column] = 0.5 if math.isclose(minimum, maximum) else (normalized[column] - minimum) / (maximum - minimum)
    band_source = normalized["fracture_strain_abs"].fillna(normalized["fracture_strain_abs"].median())
    if band_source.isna().all() or band_source.nunique(dropna=True) < 2:
        normalized["Band"] = "All rows"
    else:
        normalized["Band"] = pd.qcut(
            band_source,
            q=min(3, int(band_source.nunique(dropna=True))),
            duplicates="drop",
        ).astype(str)
    fig, ax = plt.subplots(figsize=(11, 5))
    parallel_coordinates(normalized.drop(columns=["fracture_strain_abs"]), "Band", alpha=0.35, ax=ax)
    ax.set_ylabel("Normalised value (0-1)")
    ax.set_title("Parallel coordinates")
    fig.tight_layout()
    return fig


def _sweet_spot_figure(table: pd.DataFrame) -> plt.Figure:
    if table.empty:
        return _blank_figure("Sweet spots", "Not enough assembled numeric rows for sweet-spot binning.")
    variables = list(dict.fromkeys(table["feature"].tolist()))[:6]
    rows = math.ceil(len(variables) / 2)
    fig, axes = plt.subplots(rows, 2, figsize=(12, max(4.5, rows * 3.5)))
    axes_array = np.atleast_1d(axes).ravel()
    for ax, feature in zip(axes_array, variables):
        subset = table.loc[table["feature"] == feature]
        metric = "mean_fracture_strain_abs" if subset["mean_fracture_strain_abs"].notna().any() else "mean_strain_abs"
        values = subset[metric].fillna(0.0).tolist()
        ax.bar(range(len(subset.index)), values, color="#64b5f6")
        ax.set_xticks(range(len(subset.index)))
        ax.set_xticklabels(subset["bucket"], rotation=20, ha="right", fontsize=8)
        ax.set_title(feature)
        ax.set_ylabel(metric)
    for ax in axes_array[len(variables):]:
        ax.axis("off")
    fig.tight_layout()
    return fig


def _time_drift_figure(frame: pd.DataFrame) -> plt.Figure:
    if "Production datetime" not in frame.columns:
        return _blank_figure("Time drift", "No usable production timeline rows were available.")
    working = frame.loc[frame["Production datetime"].notna()].sort_values("Production datetime").copy()
    if working.empty:
        return _blank_figure("Time drift", "No usable production timeline rows were available.")
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    with_strain = working.loc[working["strain_abs"].notna()]
    if not with_strain.empty:
        axes[0].scatter(with_strain["Production datetime"], with_strain["strain_abs"], color="#1e88e5", alpha=0.8)
        axes[0].set_ylabel("|Strain|")
        axes[0].set_title("|Strain| over time")
    if "Fracture stress (MPa)" in working.columns:
        with_fracture = working.loc[working["Fracture stress (MPa)"].notna()]
        if not with_fracture.empty:
            axes[1].scatter(with_fracture["Production datetime"], with_fracture["Fracture stress (MPa)"], color="#26a69a", alpha=0.8)
            axes[1].set_ylabel("Fracture stress (MPa)")
            axes[1].set_title("Fracture stress over time")
    for axis in axes:
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def _prepare_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    available = [column for column in CONTROL_FEATURES if column in frame.columns]
    if not available:
        return pd.DataFrame(index=frame.index), []
    numeric = frame.loc[:, available].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.loc[:, [column for column in numeric.columns if numeric[column].notna().sum() >= 2]]
    return numeric, list(numeric.columns)


def _fit_classification_model(frame: pd.DataFrame) -> tuple[pd.DataFrame, plt.Figure | None, str | None]:
    working = frame.loc[frame["is_broken"].isin([0, 1])].copy()
    class_counts = working["is_broken"].value_counts()
    if class_counts.get(0, 0) < 4 or class_counts.get(1, 0) < 4:
        return pd.DataFrame(), None, "Skipped classification: both OK and Broke need at least 4 rows."
    features, feature_names = _prepare_features(working)
    if not feature_names:
        return pd.DataFrame(), None, "Skipped classification: no usable numeric features are available."
    target = working["is_broken"].astype(int)
    pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(max_iter=2000)),
        ]
    )
    pipeline.fit(features, target)
    predicted = pipeline.predict(features)
    accuracy = float(accuracy_score(target, predicted))
    matrix = confusion_matrix(target, predicted, labels=[0, 1])
    probabilities = pipeline.predict_proba(features)[:, 1]
    auc = float(roc_auc_score(target, probabilities)) if len(set(target.tolist())) == 2 else math.nan

    metrics = pd.DataFrame(
        [
            {
                "model": "logistic_regression",
                "n": int(len(target)),
                "accuracy": round(accuracy, 4),
                "roc_auc": round(auc, 4) if not math.isnan(auc) else math.nan,
                "class_ok": int(class_counts.get(0, 0)),
                "class_broke": int(class_counts.get(1, 0)),
                "features": ", ".join(feature_names),
            }
        ]
    )

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    image = axes[0].imshow(matrix, cmap="Blues", vmin=0)
    axes[0].set_xticks([0, 1])
    axes[0].set_xticklabels([STATUS_OK, STATUS_BROKE])
    axes[0].set_yticks([0, 1])
    axes[0].set_yticklabels([STATUS_OK, STATUS_BROKE])
    axes[0].set_xlabel("Predicted")
    axes[0].set_ylabel("Actual")
    axes[0].set_title(f"Model A confusion matrix (acc={accuracy:.3f})")
    for row in range(2):
        for col in range(2):
            axes[0].text(col, row, str(matrix[row, col]), ha="center", va="center")
    fig.colorbar(image, ax=axes[0], shrink=0.9)

    axes[1].hist(probabilities[target == 0], bins=min(10, max(4, int((target == 0).sum()))), alpha=0.7, label=STATUS_OK, color=STATUS_COLORS[STATUS_OK])
    axes[1].hist(probabilities[target == 1], bins=min(10, max(4, int((target == 1).sum()))), alpha=0.7, label=STATUS_BROKE, color=STATUS_COLORS[STATUS_BROKE])
    axes[1].set_title("Predicted broke probability")
    axes[1].set_xlabel("P(Broke)")
    axes[1].legend(loc="best")
    fig.tight_layout()
    return metrics, fig, None


def _fit_regression_model(frame: pd.DataFrame, *, target: str, title: str, ok_only: bool) -> tuple[pd.DataFrame, plt.Figure | None, str | None]:
    working = frame.loc[frame[target].notna()].copy()
    if ok_only:
        working = working.loc[working["is_broken"].eq(0)]
    if len(working.index) < 8:
        return pd.DataFrame(), None, f"Skipped {title}: need at least 8 complete target rows."
    if int(working[target].nunique()) < 4:
        return pd.DataFrame(), None, f"Skipped {title}: need at least 4 distinct target values."
    features, feature_names = _prepare_features(working)
    if not feature_names:
        return pd.DataFrame(), None, f"Skipped {title}: no usable numeric features are available."

    target_series = pd.to_numeric(working[target], errors="coerce")
    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", LinearRegression()),
        ]
    )
    model.fit(features, target_series)
    predicted = model.predict(features)
    rmse = float(mean_squared_error(target_series, predicted, squared=False))
    r2 = float(r2_score(target_series, predicted))
    metrics = pd.DataFrame(
        [
            {
                "model": "linear_regression",
                "target": target,
                "n": int(len(target_series)),
                "rmse": round(rmse, 4),
                "r2": round(r2, 4),
                "features": ", ".join(feature_names),
            }
        ]
    )

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    axes[0].scatter(target_series, predicted, color="#5b9bd5", edgecolors="white", linewidths=0.6)
    minimum = float(min(target_series.min(), predicted.min()))
    maximum = float(max(target_series.max(), predicted.max()))
    axes[0].plot([minimum, maximum], [minimum, maximum], linestyle="--", color="#6c757d")
    axes[0].set_xlabel("Actual")
    axes[0].set_ylabel("Predicted")
    axes[0].set_title(f"{title}: actual vs predicted")

    residuals = target_series - predicted
    axes[1].hist(residuals, bins=min(10, max(4, len(residuals))), color="#8f63c8", edgecolor="white")
    axes[1].axvline(0, color="black", linestyle="--", linewidth=1)
    axes[1].set_xlabel("Residual")
    axes[1].set_title(f"{title}: residuals (RMSE={rmse:.3f}, R²={r2:.3f})")
    fig.tight_layout()
    return metrics, fig, None


def _write_summary_workbook(*, output_path: Path, canonical_frame: pd.DataFrame, tables: Mapping[str, pd.DataFrame]) -> None:
    with pd.ExcelWriter(output_path) as writer:
        canonical_frame.to_excel(writer, sheet_name="canonical_data", index=False)
        for sheet_name in SUMMARY_TABLE_ORDER:
            table = tables.get(sheet_name)
            if isinstance(table, pd.DataFrame):
                table.to_excel(writer, sheet_name=_slugify(sheet_name)[:31], index=False)


def _write_manifest(
    *,
    path: Path,
    config: MicrowireEdaConfig,
    input_kind: str,
    tables: Mapping[str, pd.DataFrame],
    figures: Sequence[FigureArtifact],
    skipped_sections: Mapping[str, str],
    row_counts: Mapping[str, int],
    report_path: Path,
    workbook_path: Path,
    csv_path: Path,
    pdf_path: Path | None,
) -> None:
    payload = {
        "kind": "MicrowireEDA",
        "version": 1,
        "input_path": str(config.input_path) if config.input_path else None,
        "input_kind": input_kind,
        "row_scope": config.row_scope,
        "report_title": config.report_title,
        "output_dir": str(path.parent),
        "report_path": str(report_path),
        "workbook_path": str(workbook_path),
        "csv_path": str(csv_path),
        "pdf_path": str(pdf_path) if pdf_path else None,
        "row_counts": {str(key): int(value) for key, value in row_counts.items()},
        "tables": {str(name): {"rows": int(table.shape[0]), "columns": int(table.shape[1])} for name, table in tables.items()},
        "figures": [
            {
                "key": figure.key,
                "title": figure.title,
                "section": figure.section,
                "png_path": str(figure.png_path) if figure.png_path is not None else None,
            }
            for figure in figures
        ],
        "skipped_sections": dict(skipped_sections),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _html_document(
    *,
    title: str,
    row_scope_table: pd.DataFrame,
    figures: Sequence[FigureArtifact],
    tables: Mapping[str, pd.DataFrame],
    skipped_sections: Mapping[str, str],
) -> str:
    grouped_figures: dict[str, list[FigureArtifact]] = {}
    for figure in figures:
        grouped_figures.setdefault(figure.section, []).append(figure)

    sections = [
        ("coverage", "Coverage"),
        ("outcomes", "Outcome overview"),
        ("fabrication", "Fabrication and geometry"),
        ("relationships", "Relationships to function"),
        ("interactions", "Interactions"),
        ("sweet_spots", "Sweet spots"),
        ("time", "Time drift"),
        ("models", "Models"),
    ]
    parts: list[str] = []
    for section_key, heading in sections:
        parts.append(f"<section><h2>{html.escape(heading)}</h2>")
        if section_key in skipped_sections:
            parts.append(f"<p class='skip-note'>{html.escape(skipped_sections[section_key])}</p>")
        for figure in grouped_figures.get(section_key, []):
            parts.append(f"<article class='figure-card'><h3>{html.escape(figure.title)}</h3>{figure.html}</article>")
        if section_key == "coverage":
            parts.append("<h3>Coverage table</h3>")
            parts.append(_table_html(tables["coverage"]))
            parts.append("<h3>Row scope</h3>")
            parts.append(_table_html(row_scope_table))
        elif section_key == "relationships":
            for key, label in [
                ("strain_correlations", "Top correlations to |Strain|"),
                ("fracture_strain_correlations", "Top correlations to fracture |strain|"),
                ("broke_ok_summary", "Broke vs OK summary"),
            ]:
                table = tables.get(key)
                if isinstance(table, pd.DataFrame) and not table.empty:
                    parts.append(f"<h3>{html.escape(label)}</h3>")
                    parts.append(_table_html(table))
        elif section_key == "sweet_spots":
            table = tables.get("sweet_spots")
            if isinstance(table, pd.DataFrame) and not table.empty:
                parts.append("<h3>Sweet spot bins</h3>")
                parts.append(_table_html(table))
        elif section_key == "models":
            for key in ("model_a_metrics", "model_b_metrics", "model_c_metrics"):
                table = tables.get(key)
                if isinstance(table, pd.DataFrame) and not table.empty:
                    parts.append(f"<h3>{html.escape(key.replace('_', ' ').title())}</h3>")
                    parts.append(_table_html(table))
        parts.append("</section>")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{
      font-family: "Segoe UI", Arial, sans-serif;
      margin: 24px auto;
      max-width: 1320px;
      color: #1f2933;
      background: linear-gradient(180deg, #f5f7fb 0%, #ffffff 18%);
      padding: 0 20px 40px;
    }}
    h1, h2, h3 {{ color: #17324d; }}
    .lead {{ color: #52606d; margin-bottom: 22px; }}
    section {{
      background: #ffffff;
      border: 1px solid #d9e2ec;
      border-radius: 14px;
      padding: 18px 20px 22px;
      margin: 18px 0;
      box-shadow: 0 10px 22px rgba(15, 23, 42, 0.04);
    }}
    .figure-card {{
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
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="lead">Read-only exploratory analysis built from Microwire Data Builder assemble data.</p>
  {''.join(parts)}
</body>
</html>"""


def run_microwire_eda(config: MicrowireEdaConfig) -> MicrowireEdaResult:
    raw_frame, input_kind = load_input_frame(config)
    canonical_frame = canonicalise_frame(raw_frame)
    scoped_frame, applied_scope = apply_row_scope(canonical_frame, config)
    scoped_frame = _ordered_export_frame(scoped_frame)

    output_dir = _normalise_output_dir(config).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    report_path = output_dir / "microwire_eda_report.html"
    workbook_path = output_dir / "microwire_eda_summary.xlsx"
    csv_path = output_dir / "microwire_eda_canonical.csv"
    manifest_path = output_dir / "microwire_eda_manifest.json"
    pdf_path = output_dir / "microwire_eda_report.pdf" if config.export_pdf_bundle else None

    tables: dict[str, pd.DataFrame] = {
        "coverage": _coverage_table(scoped_frame),
        "row_scope": _row_scope_table(config.row_scope, applied_scope, _usable_row_counts(scoped_frame)),
        "spearman_all": _spearman_table(scoped_frame, CONTROL_FEATURES),
        "strain_correlations": _target_correlation_table(scoped_frame, "strain_abs"),
        "fracture_strain_correlations": _target_correlation_table(scoped_frame, "fracture_strain_abs"),
        "broke_ok_summary": _broke_ok_summary(scoped_frame),
        "sweet_spots": _sweet_spot_table(scoped_frame),
    }
    skipped_sections: dict[str, str] = {}
    figures: list[FigureArtifact] = []

    pdf = PdfPages(pdf_path) if pdf_path is not None else None
    try:
        figures.append(_save_figure(_coverage_figure(scoped_frame), output_dir=figures_dir, key="coverage", title="Coverage heatmap", section="coverage", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_outcome_overview_figure(scoped_frame), output_dir=figures_dir, key="outcomes", title="Outcome overview", section="outcomes", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_distribution_grid_figure(scoped_frame, FABRICATION_COLUMNS + GEOMETRY_COLUMNS, "Fabrication and geometry distributions"), output_dir=figures_dir, key="fabrication_distributions", title="Fabrication and geometry distributions", section="fabrication", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_annotated_heatmap(_correlation_matrix(scoped_frame, CONTROL_FEATURES), title="Spearman correlation matrix"), output_dir=figures_dir, key="fabrication_heatmap", title="Spearman correlation matrix", section="fabrication", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_annotated_heatmap(_correlation_matrix(scoped_frame, GEOMETRY_COLUMNS + CONTROL_FEATURES[:4]), title="Controllable parameter interactions"), output_dir=figures_dir, key="fabrication_interactions", title="Controllable parameter interactions", section="fabrication", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_scatter_grid(scoped_frame, target="strain_abs", title="Top variables vs |Strain|"), output_dir=figures_dir, key="strain_scatter", title="Top variables vs |Strain|", section="relationships", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_geometry_to_function_figure(scoped_frame), output_dir=figures_dir, key="geometry_to_function", title="Geometry to function", section="relationships", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_pairplot_figure(scoped_frame), output_dir=figures_dir, key="pairplot", title="Pairplot", section="interactions", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_interaction_grid_figure(scoped_frame), output_dir=figures_dir, key="interaction_grid", title="Interaction grid", section="interactions", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_parallel_coordinates_figure(scoped_frame), output_dir=figures_dir, key="parallel_coordinates", title="Parallel coordinates", section="interactions", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_sweet_spot_figure(tables["sweet_spots"]), output_dir=figures_dir, key="sweet_spots", title="Sweet spots", section="sweet_spots", pdf=pdf, write_png=config.export_png_bundle))
        figures.append(_save_figure(_time_drift_figure(scoped_frame), output_dir=figures_dir, key="time_drift", title="Time drift", section="time", pdf=pdf, write_png=config.export_png_bundle))

        tables["model_a_metrics"], model_a_fig, model_a_skip = _fit_classification_model(scoped_frame)
        if model_a_fig is not None:
            figures.append(_save_figure(model_a_fig, output_dir=figures_dir, key="model_a", title="Model A: broke classifier", section="models", pdf=pdf, write_png=config.export_png_bundle))
        if model_a_skip:
            skipped_sections["models"] = model_a_skip

        tables["model_b_metrics"], model_b_fig, model_b_skip = _fit_regression_model(scoped_frame, target="strain_abs", title="Model B", ok_only=True)
        if model_b_fig is not None:
            figures.append(_save_figure(model_b_fig, output_dir=figures_dir, key="model_b", title="Model B: strain regression", section="models", pdf=pdf, write_png=config.export_png_bundle))
        if model_b_skip:
            skipped_sections["models_b"] = model_b_skip

        tables["model_c_metrics"], model_c_fig, model_c_skip = _fit_regression_model(scoped_frame, target="fracture_strain_abs", title="Model C", ok_only=False)
        if model_c_fig is not None:
            figures.append(_save_figure(model_c_fig, output_dir=figures_dir, key="model_c", title="Model C: fracture strain regression", section="models", pdf=pdf, write_png=config.export_png_bundle))
        if model_c_skip:
            skipped_sections["models_c"] = model_c_skip
    finally:
        if pdf is not None:
            pdf.close()

    if "models" not in skipped_sections:
        model_messages = [message for key, message in skipped_sections.items() if key.startswith("models_")]
        if model_messages:
            skipped_sections["models"] = " ".join(model_messages)

    scoped_frame.to_csv(csv_path, index=False)
    _write_summary_workbook(output_path=workbook_path, canonical_frame=scoped_frame, tables=tables)
    report_path.write_text(
        _html_document(
            title=config.report_title,
            row_scope_table=tables["row_scope"],
            figures=figures,
            tables=tables,
            skipped_sections=skipped_sections,
        ),
        encoding="utf-8",
    )

    row_counts = {
        "input_rows": int(len(canonical_frame.index)),
        "analysed_rows": int(len(scoped_frame.index)),
        "labeled_rows": int(scoped_frame.get("is_broken", pd.Series(dtype=float)).isin([0, 1]).sum()),
        "numeric_strain_rows": int(scoped_frame.get("strain_abs", pd.Series(dtype=float)).notna().sum()),
        "fracture_strain_rows": int(scoped_frame.get("fracture_strain_abs", pd.Series(dtype=float)).notna().sum()),
    }
    row_counts["all_rows"] = row_counts["analysed_rows"]
    row_counts["known_outcome"] = row_counts["labeled_rows"]
    _write_manifest(
        path=manifest_path,
        config=config,
        input_kind=input_kind,
        tables=tables,
        figures=figures,
        skipped_sections=skipped_sections,
        row_counts=row_counts,
        report_path=report_path,
        workbook_path=workbook_path,
        csv_path=csv_path,
        pdf_path=pdf_path if pdf_path and pdf_path.exists() else None,
    )
    return MicrowireEdaResult(
        config=config,
        input_kind=input_kind,
        output_dir=output_dir,
        report_path=report_path,
        workbook_path=workbook_path,
        csv_path=csv_path,
        manifest_path=manifest_path,
        pdf_path=pdf_path if pdf_path and pdf_path.exists() else None,
        figure_paths=[figure.png_path for figure in figures if figure.png_path is not None],
        skipped_sections=skipped_sections,
        row_counts=row_counts,
        tables=tables,
    )


generate_report = run_microwire_eda


def _classification_model(frame: pd.DataFrame) -> tuple[pd.DataFrame, plt.Figure | None, str | None]:
    working = frame.loc[frame["is_broken"].isin([0, 1])].copy()
    if working.empty:
        return pd.DataFrame(), None, "Classification skipped: no rows with a known broke/OK outcome."
    counts = working["is_broken"].value_counts()
    if int(counts.get(0, 0)) < 4 or int(counts.get(1, 0)) < 4:
        return pd.DataFrame(), None, "Classification skipped: at least 4 OK rows and 4 Broke rows are required."
    features = [feature for feature in CONTROL_FEATURES if feature in working.columns and int(working[feature].notna().sum()) >= 8]
    if not features:
        return pd.DataFrame(), None, "Classification skipped: no control features had enough usable rows."
    complete = working.dropna(subset=features + ["is_broken"]).copy()
    if len(complete.index) < 8:
        return pd.DataFrame(), None, "Classification skipped: at least 8 complete rows are required after feature filtering."
    x = complete[features]
    y = complete["is_broken"].astype(int)
    pipeline = Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler()), ("model", LogisticRegression(max_iter=2000, class_weight="balanced"))])
    pipeline.fit(x, y)
    predicted = pipeline.predict(x)
    probabilities = pipeline.predict_proba(x)[:, 1]
    matrix = confusion_matrix(y, predicted, labels=[0, 1])
    metrics = pd.DataFrame([{"model": "Model A", "rows_used": len(complete.index), "feature_count": len(features), "accuracy": accuracy_score(y, predicted), "roc_auc": roc_auc_score(y, probabilities), "tn": int(matrix[0, 0]), "fp": int(matrix[0, 1]), "fn": int(matrix[1, 0]), "tp": int(matrix[1, 1])}])
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].scatter(probabilities, y + np.random.normal(0.0, 0.03, size=len(y)), c=[STATUS_COLORS[STATUS_BROKE] if value == 1 else STATUS_COLORS[STATUS_OK] for value in y], alpha=0.8)
    axes[0].set_title("Model A: predicted broke probability")
    axes[0].set_xlabel("Predicted probability")
    axes[0].set_ylabel("Actual outcome")
    axes[0].set_yticks([0, 1], labels=[STATUS_OK, STATUS_BROKE])
    coefficients = pd.Series(pipeline.named_steps["model"].coef_[0], index=features).sort_values()
    axes[1].barh(coefficients.index, coefficients.values, color=[STATUS_COLORS[STATUS_BROKE] if value > 0 else STATUS_COLORS[STATUS_OK] for value in coefficients.values])
    axes[1].axvline(0.0, color="#37474f", linewidth=1.0)
    axes[1].set_title("Model A: logistic coefficients")
    fig.tight_layout()
    return metrics, fig, None


def _regression_model(frame: pd.DataFrame, target: str, label: str, model_name: str) -> tuple[pd.DataFrame, plt.Figure | None, str | None]:
    if target not in frame.columns:
        return pd.DataFrame(), None, f"{model_name} skipped: target column is unavailable."
    working = frame.loc[frame[target].notna()].copy()
    if working.empty:
        return pd.DataFrame(), None, f"{model_name} skipped: no rows with numeric target values are available."
    features = [feature for feature in CONTROL_FEATURES if feature in working.columns and int(working[feature].notna().sum()) >= 8]
    if not features:
        return pd.DataFrame(), None, f"{model_name} skipped: no control features had enough usable rows."
    complete = working.dropna(subset=features + [target]).copy()
    if len(complete.index) < 8:
        return pd.DataFrame(), None, f"{model_name} skipped: at least 8 complete rows are required."
    if complete[target].nunique(dropna=True) < 4:
        return pd.DataFrame(), None, f"{model_name} skipped: at least 4 distinct target values are required."
    x = complete[features]
    y = complete[target]
    pipeline = Pipeline([("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler()), ("model", LinearRegression())])
    pipeline.fit(x, y)
    predicted = pd.Series(pipeline.predict(x), index=complete.index)
    residuals = y - predicted
    metrics = pd.DataFrame([{"model": model_name, "target": label, "rows_used": len(complete.index), "feature_count": len(features), "rmse": math.sqrt(mean_squared_error(y, predicted)), "r2": r2_score(y, predicted)}])
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].scatter(y, predicted, c=[STATUS_COLORS[STATUS_BROKE] if value == 1 else STATUS_COLORS[STATUS_OK] if value == 0 else STATUS_COLORS[STATUS_NO_DATA] for value in complete.get("is_broken", pd.Series([math.nan] * len(complete.index)))], alpha=0.8)
    lower = min(float(y.min()), float(predicted.min()))
    upper = max(float(y.max()), float(predicted.max()))
    axes[0].plot([lower, upper], [lower, upper], linestyle="--", color="#37474f")
    axes[0].set_title(f"{model_name}: prediction vs reality")
    axes[0].set_xlabel(f"Actual {label}")
    axes[0].set_ylabel(f"Predicted {label}")
    axes[1].hist(residuals, bins=min(10, max(4, len(residuals))), color="#4f83cc", alpha=0.85)
    axes[1].axvline(0.0, linestyle="--", color="#37474f")
    axes[1].set_title(f"{model_name}: residuals")
    axes[1].set_xlabel("Residual")
    fig.tight_layout()
    return metrics, fig, None


def _write_html_report(
    *,
    report_path: Path,
    title: str,
    cards: Sequence[tuple[str, Any]],
    sections: Sequence[tuple[str, str, list[str], list[str]]],
) -> None:
    html_parts = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title>",
        "<style>",
        "body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f7fafc;color:#1f2933;}",
        "header{padding:28px 32px;background:linear-gradient(135deg,#0f4c81,#147a73);color:#fff;}",
        "main{padding:24px 32px 40px;max-width:1400px;margin:0 auto;}",
        "section{background:#fff;border-radius:14px;padding:20px 22px;margin-bottom:22px;box-shadow:0 1px 4px rgba(15,23,42,.08);}",
        "figure{margin:18px 0;}figure img{max-width:100%;border:1px solid #d9e2ec;border-radius:8px;}",
        ".note{background:#fff4e5;border-left:4px solid #fb8c00;padding:10px 12px;margin:12px 0;}",
        ".eda-table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px;}",
        ".eda-table th,.eda-table td{border:1px solid #d9e2ec;padding:7px 8px;text-align:left;vertical-align:top;}",
        ".eda-table th{background:#f0f4f8;}",
        ".cards{display:flex;flex-wrap:wrap;gap:12px;margin:0 0 22px;}",
        ".card{background:#fff;border-radius:12px;padding:14px 16px;min-width:160px;box-shadow:0 1px 4px rgba(15,23,42,.08);}",
        ".label{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#52606d;}",
        ".value{font-size:26px;font-weight:600;margin-top:5px;}",
        "</style></head><body>",
        f"<header><h1>{html.escape(title)}</h1><p>Read-only exploratory analysis of assemble data with coverage checks, outcome summaries, controllable-parameter views, interaction plots, sweet-spot summaries, time drift, and gated baseline models.</p></header>",
        "<main><div class='cards'>",
    ]
    for label, value in cards:
        html_parts.append(f"<div class='card'><div class='label'>{html.escape(str(label))}</div><div class='value'>{html.escape(str(value))}</div></div>")
    html_parts.append("</div>")
    for section_title, intro, figures, tables in sections:
        html_parts.append(f"<section><h2>{html.escape(section_title)}</h2><p>{html.escape(intro)}</p>")
        html_parts.extend(figures)
        html_parts.extend(tables)
        html_parts.append("</section>")
    html_parts.append("</main></body></html>")
    report_path.write_text("\n".join(html_parts), encoding="utf-8")


def generate_report(
    config: MicrowireEdaConfig,
    progress_callback: callable | None = None,
) -> MicrowireEdaResult:
    progress = progress_callback or (lambda _message: None)
    progress("Loading assemble data")
    raw_frame, input_kind = load_input_frame(config)
    progress("Canonicalising columns")
    canonical = canonicalise_frame(raw_frame)
    scoped_frame, applied_scope = apply_row_scope(canonical, config)
    output_dir = _normalise_output_dir(config).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / "figures_bundle.pdf" if config.export_pdf_bundle else None
    pdf = PdfPages(pdf_path) if pdf_path is not None else None
    try:
        progress("Preparing summary tables")
        counts = _usable_row_counts(scoped_frame)
        counts["fracture_stress_rows"] = int(
            scoped_frame.get("Fracture stress (MPa)", pd.Series(dtype=float)).notna().sum()
        )
        tables: dict[str, pd.DataFrame] = {
            "coverage": _coverage_table(scoped_frame),
            "row_scope": _row_scope_table(config.row_scope, applied_scope, counts),
            "spearman_all": _spearman_table(scoped_frame, CONTROL_FEATURES),
            "strain_correlations": _target_correlation_table(scoped_frame, "strain_abs"),
            "fracture_strain_correlations": _target_correlation_table(scoped_frame, "fracture_strain_abs"),
            "fracture_stress_correlations": _target_correlation_table(scoped_frame, "Fracture stress (MPa)"),
            "sweet_spots": _sweet_spot_table(scoped_frame),
        }
        skipped_sections: dict[str, str] = {}
        figures_dir = output_dir / "figures"
        figure_artifacts: list[FigureArtifact] = []
        progress("Building coverage and distribution views")
        figure_artifacts.append(_save_figure(_coverage_figure(scoped_frame), output_dir=figures_dir, key="coverage_heatmap", title="Coverage heatmap", section="coverage", pdf=pdf, write_png=config.export_png_bundle))
        figure_artifacts.append(_save_figure(_outcome_overview_figure(scoped_frame), output_dir=figures_dir, key="outcome_overview", title="Outcome overview", section="outcomes", pdf=pdf, write_png=config.export_png_bundle))
        figure_artifacts.append(_save_figure(_distribution_grid_figure(scoped_frame, GEOMETRY_COLUMNS + FABRICATION_COLUMNS, "Geometry and fabrication distributions"), output_dir=figures_dir, key="distributions", title="Geometry and fabrication distributions", section="fabrication", pdf=pdf, write_png=config.export_png_bundle))
        corr_matrix = _correlation_matrix(scoped_frame, CONTROL_FEATURES)
        figure_artifacts.append(_save_figure(_annotated_heatmap(corr_matrix, title="Spearman correlations"), output_dir=figures_dir, key="spearman_heatmap", title="Spearman correlations", section="fabrication", pdf=pdf, write_png=config.export_png_bundle))
        progress("Building target correlation views")
        for key, table_name, title_text in [
            ("strain_correlation_bar", "strain_correlations", "Top correlations with |Strain|"),
            ("fracture_correlation_bar", "fracture_strain_correlations", "Top correlations with |Fracture strain|"),
            ("fracture_stress_correlation_bar", "fracture_stress_correlations", "Top correlations with fracture stress"),
        ]:
            table = tables[table_name]
            if table.empty:
                skipped_sections[key] = f"Not enough rows for {title_text.lower()}."
                continue
            display = table.head(8).iloc[::-1]
            fig, ax = plt.subplots(figsize=(9, max(4, len(display) * 0.65)))
            colors = [STATUS_COLORS[STATUS_OK] if value >= 0 else STATUS_COLORS[STATUS_BROKE] for value in display["rho"]]
            ax.barh(display["feature"], display["rho"], color=colors)
            ax.axvline(0, color="black", linewidth=1)
            ax.set_xlabel("Spearman ρ")
            ax.set_title(title_text)
            fig.tight_layout()
            figure_artifacts.append(_save_figure(fig, output_dir=figures_dir, key=key, title=title_text, section="function", pdf=pdf, write_png=config.export_png_bundle))
        figure_artifacts.append(_save_figure(_scatter_grid(scoped_frame, target="strain_abs", title="Top |Strain| scatter plots"), output_dir=figures_dir, key="strain_scatter_grid", title="Top |Strain| scatter plots", section="function", pdf=pdf, write_png=config.export_png_bundle))
        figure_artifacts.append(_save_figure(_geometry_to_function_figure(scoped_frame), output_dir=figures_dir, key="geometry_to_function", title="Geometry to strain", section="geometry", pdf=pdf, write_png=config.export_png_bundle))
        progress("Building interaction, sweet spot, and time drift views")
        figure_artifacts.append(_save_figure(_pairplot_figure(scoped_frame), output_dir=figures_dir, key="pairplot", title="Pairplot", section="interactions", pdf=pdf, write_png=config.export_png_bundle))
        figure_artifacts.append(_save_figure(_interaction_grid_figure(scoped_frame), output_dir=figures_dir, key="interaction_grid", title="Interaction grid", section="interactions", pdf=pdf, write_png=config.export_png_bundle))
        figure_artifacts.append(_save_figure(_parallel_coordinates_figure(scoped_frame), output_dir=figures_dir, key="parallel_coordinates", title="Parallel coordinates", section="interactions", pdf=pdf, write_png=config.export_png_bundle))
        figure_artifacts.append(_save_figure(_sweet_spot_figure(tables["sweet_spots"]), output_dir=figures_dir, key="sweet_spots", title="Sweet spots", section="sweet_spots", pdf=pdf, write_png=config.export_png_bundle))
        figure_artifacts.append(_save_figure(_time_drift_figure(scoped_frame), output_dir=figures_dir, key="time_drift", title="Time drift", section="time_drift", pdf=pdf, write_png=config.export_png_bundle))
        progress("Running regression models")
        model_b_metrics, model_b_fig, model_b_skip = _regression_model(scoped_frame, "strain_abs", "|Strain|", "Model B")
        model_c_metrics, model_c_fig, model_c_skip = _regression_model(scoped_frame, "fracture_strain_abs", "|Fracture strain|", "Model C")
        model_d_metrics, model_d_fig, model_d_skip = _regression_model(
            scoped_frame,
            "Fracture stress (MPa)",
            "Fracture stress (MPa)",
            "Model D",
        )
        tables["model_b_metrics"] = model_b_metrics
        tables["model_c_metrics"] = model_c_metrics
        tables["model_d_metrics"] = model_d_metrics
        for skip_key, reason in [("model_b", model_b_skip), ("model_c", model_c_skip), ("model_d", model_d_skip)]:
            if reason:
                skipped_sections[skip_key] = reason
        for key, title_text, fig in [
            ("model_b", "Model B - |Strain| regression", model_b_fig),
            ("model_c", "Model C - |Fracture strain| regression", model_c_fig),
            ("model_d", "Model D - Fracture stress regression", model_d_fig),
        ]:
            if fig is not None:
                figure_artifacts.append(_save_figure(fig, output_dir=figures_dir, key=key, title=title_text, section="models", pdf=pdf, write_png=config.export_png_bundle))
    finally:
        if pdf is not None:
            pdf.close()

    progress("Writing output files")
    ordered_frame = _ordered_export_frame(scoped_frame)
    csv_path = output_dir / "canonical_dataset.csv"
    ordered_frame.to_csv(csv_path, index=False)
    workbook_path = output_dir / "summary_tables.xlsx"
    with pd.ExcelWriter(workbook_path) as writer:
        ordered_frame.to_excel(writer, sheet_name="dataset", index=False)
        for name in SUMMARY_TABLE_ORDER:
            table = tables.get(name)
            if isinstance(table, pd.DataFrame):
                table.to_excel(writer, sheet_name=_slugify(name)[:31], index=False)

    sections = [
        ("Coverage report", "Completeness, availability, and usable-row counts for the assembled dataset.", [artifact.html for artifact in figure_artifacts if artifact.section == "coverage"], [_table_html(tables["coverage"]), _table_html(tables["row_scope"])]),
        ("Endpoint overview", "Distributions for strain, fracture strain, stress, and fracture stress measured in the assembled data.", [artifact.html for artifact in figure_artifacts if artifact.section == "outcomes"], []),
        ("Fabrication to geometry", "Distribution and correlation views for geometry and fabrication variables.", [artifact.html for artifact in figure_artifacts if artifact.section == "fabrication"], [_table_html(tables["spearman_all"])]),
        ("Geometry and fabrication to function", "Target correlations against the available strain and fracture endpoints.", [artifact.html for artifact in figure_artifacts if artifact.section == "function"], [_table_html(tables["strain_correlations"]), _table_html(tables["fracture_strain_correlations"]), _table_html(tables["fracture_stress_correlations"])]),
        ("Geometry to strain", "Dedicated d, D, and d/D views against strain endpoints.", [artifact.html for artifact in figure_artifacts if artifact.section == "geometry"], []),
        ("Interaction views", "Pairwise, interaction-grid, and parallel-coordinate views for overlapping assembled rows.", [artifact.html for artifact in figure_artifacts if artifact.section == "interactions"], []),
        ("Sweet spots", "Quantile-binned summaries for strain and fracture endpoints by parameter band.", [artifact.html for artifact in figure_artifacts if artifact.section == "sweet_spots"], [_table_html(tables["sweet_spots"])]),
        ("Time drift", "Strain and fracture stress drift along the production timeline.", [artifact.html for artifact in figure_artifacts if artifact.section == "time_drift"], []),
        ("Regression models", "Gated baseline regressions for strain, fracture strain, and fracture stress.", [artifact.html for artifact in figure_artifacts if artifact.section == "models"], [_table_html(tables["model_b_metrics"]), _table_html(tables["model_c_metrics"]), _table_html(tables["model_d_metrics"])]),
    ]
    report_path = output_dir / "report.html"
    cards = [("Rows", counts["all_rows"]), ("Numeric |Strain|", counts["numeric_strain"]), ("Numeric |Fracture strain|", counts["numeric_fracture_strain"]), ("Numeric fracture stress", counts["fracture_stress_rows"])]
    _write_html_report(report_path=report_path, title=config.report_title, cards=cards, sections=sections)
    manifest_path = output_dir / "manifest.json"
    note = None
    if config.row_scope != applied_scope:
        note = f"Requested row scope '{config.row_scope}' was not available from the current input, so '{applied_scope}' was used instead."
    manifest = {
        "kind": "microwire_eda",
        "version": 1,
        "report_title": config.report_title,
        "input_kind": input_kind,
        "input_path": str(config.input_path.resolve()) if isinstance(config.input_path, Path) else None,
        "output_dir": str(output_dir),
        "row_scope_requested": config.row_scope,
        "row_scope_applied": applied_scope,
        "row_counts": counts,
        "figure_paths": [str(artifact.png_path) for artifact in figure_artifacts if artifact.png_path is not None],
        "skipped_sections": skipped_sections,
        "note": note,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    progress("Finished")
    return MicrowireEdaResult(
        config=config,
        input_kind=input_kind,
        output_dir=output_dir,
        report_path=report_path,
        workbook_path=workbook_path,
        csv_path=csv_path,
        manifest_path=manifest_path,
        pdf_path=pdf_path if pdf_path is not None and pdf_path.exists() else None,
        figure_paths=[artifact.png_path for artifact in figure_artifacts if artifact.png_path is not None],
        skipped_sections=skipped_sections,
        row_counts=counts,
        tables=tables,
    )
