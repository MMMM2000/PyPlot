from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Iterable, List, NamedTuple, Sequence, Tuple

import pandas as pd
from matplotlib import cm

from microwire_data_builder.core import _parse_numeric, _parse_strain_float


def clean_header(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def pretty_header(value: object, index: int) -> str:
    cleaned = clean_header(value)
    if cleaned and not cleaned.lower().startswith("unnamed"):
        return cleaned
    return f"Column {index + 1}"


def extract_element_counts(composition: str) -> dict[str, float]:
    counts = {"Ni": 0.0, "Fe": 0.0, "Ga": 0.0, "Co": 0.0}
    if not composition:
        return counts
    for element, value in re.findall(r"(Ni|Fe|Ga|Co)\s*(\d+(?:\.\d+)?)", composition):
        try:
            counts[element] = float(value)
        except ValueError:
            continue
    return counts


def build_column_map(columns: Sequence[object]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, header in enumerate(columns):
        name = clean_header(header)
        lowered = name.lower()
        if not lowered:
            continue
        if "composition" in lowered:
            mapping.setdefault("composition", idx)
        elif "microwire" in lowered or "wire" in lowered:
            mapping.setdefault("microwire", idx)
        elif "strain" in lowered or "%" in lowered:
            mapping.setdefault("strain", idx)
        elif "status" in lowered or "broke" in lowered:
            mapping.setdefault("status", idx)
    return mapping


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not pd.isna(value):
        return str(value).strip()
    if pd.isna(value):
        return ""
    return str(value).strip()


class PlotConfig(NamedTuple):
    labels: Tuple[str, ...]
    dimension: int


def auto_plot_combinations(
    labels: Iterable[str],
    strain_label: str,
    include_2d: bool,
    include_3d: bool,
) -> List[PlotConfig]:
    label_list = list(dict.fromkeys(labels))
    combos: List[PlotConfig] = []
    if include_2d:
        for combo in itertools.combinations(label_list, 2):
            if strain_label in combo:
                combos.append(PlotConfig(combo, 2))
    if include_3d:
        for combo in itertools.combinations(label_list, 3):
            if strain_label in combo:
                combos.append(PlotConfig(combo, 3))
    return combos


def build_plot_dataframe(path: Path) -> tuple[pd.DataFrame, str, list[str]]:
    df = pd.read_excel(path).dropna(how="all")
    if df.empty:
        raise ValueError("The worksheet does not contain any data.")
    columns = list(df.columns)
    mapping = build_column_map(columns)
    composition_idx = mapping.get("composition") if mapping.get("composition") is not None else (0 if columns else None)
    microwire_idx = mapping.get("microwire") if mapping.get("microwire") is not None else (1 if len(columns) > 1 else None)
    strain_idx = mapping.get("strain")
    status_idx = mapping.get("status")
    if strain_idx is None:
        raise ValueError("Could not locate a strain column.")
    strain_label = pretty_header(columns[strain_idx], strain_idx)
    numeric_columns = []
    for idx, header in enumerate(columns):
        if idx in {strain_idx, composition_idx, microwire_idx, status_idx}:
            continue
        label = pretty_header(header, idx)
        lowered = label.lower()
        if "m length" in lowered or "a length" in lowered or "file" in lowered:
            continue
        numeric_columns.append((idx, label))
    records = []
    for row_index, row in df.iterrows():
        status_value = clean_cell(row.iloc[status_idx]) if status_idx is not None else ""
        if status_value.lower().startswith("broke"):
            continue
        strain_value = _parse_strain_float(row.iloc[strain_idx])
        if strain_value is None:
            continue
        microwire_label = clean_cell(row.iloc[microwire_idx]) if microwire_idx is not None else ""
        composition_label = clean_cell(row.iloc[composition_idx]) if composition_idx is not None else ""
        record = {
            "Microwire": microwire_label or composition_label or f"Row {row_index}",
            "Composition": composition_label,
            strain_label: strain_value,
        }
        for col_idx, label in numeric_columns:
            record[label] = _parse_numeric(row.iloc[col_idx])
        for element, value in extract_element_counts(composition_label).items():
            record[f"{element} (%)"] = value
        records.append(record)
    plot_df = pd.DataFrame(records)
    valid_labels = [
        label
        for label in plot_df.columns
        if label not in {"Microwire", "Composition"}
        and plot_df[label].notna().sum() >= 2
        and plot_df[label].dropna().nunique() >= 2
    ]
    if strain_label not in valid_labels:
        valid_labels.insert(0, strain_label)
    return plot_df, strain_label, valid_labels


def build_plot_configs(
    *,
    valid_labels: list[str],
    strain_label: str,
    automatic: bool,
    include_2d: bool,
    include_3d: bool,
    manual_dimension: int,
    manual_labels: tuple[str, str, str | None],
) -> list[PlotConfig]:
    if automatic:
        return auto_plot_combinations(valid_labels, strain_label, include_2d, include_3d)
    x_axis, y_axis, z_axis = manual_labels
    if not x_axis or not y_axis:
        return []
    if manual_dimension == 3:
        if not z_axis:
            return []
        return [PlotConfig((x_axis, y_axis, z_axis), 3)]
    return [PlotConfig((x_axis, y_axis), 2)]


def draw_scatter(ax, subset: pd.DataFrame, labels: Tuple[str, ...]) -> None:
    xs = subset[labels[0]].to_numpy(dtype=float)
    ys = subset[labels[1]].to_numpy(dtype=float)
    labels_text = subset["Microwire"].tolist()
    if xs.max() != xs.min():
        norm = (xs - xs.min()) / (xs.max() - xs.min())
    else:
        norm = [0.5] * len(xs)
    colors = [tuple(rgba) for rgba in cm.viridis(norm)]
    if len(labels) == 3:
        zs = subset[labels[2]].to_numpy(dtype=float)
        ax.scatter(xs, ys, zs, c=colors, s=60, depthshade=True)
        for x, y, z, label_text in zip(xs, ys, zs, labels_text):
            ax.text(x, y, z, label_text, fontsize=9)
        ax.set_zlabel(labels[2])
    else:
        ax.scatter(xs, ys, c=colors, s=60)
        for x, y, label_text in zip(xs, ys, labels_text):
            ax.text(x, y, label_text, fontsize=9)
    ax.set_xlabel(labels[0])
    ax.set_ylabel(labels[1])
    ax.set_title(" vs ".join(labels))
