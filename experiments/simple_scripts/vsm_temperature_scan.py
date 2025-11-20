#!/usr/bin/env python3
"""Tkinter UI for plotting VSM temperature scan data (Signal X vs Temperature)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple, cast

import pandas as pd
import tkinter as tk  # noqa: F401 - required for Tk dialogs used in _shared
from tkinter import ttk
import numpy as np
import json

try:
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - optional dependency
    plt = None

try:
    import originpro as op  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    op = None

try:
    from experiments.simple_scripts._shared import SimpleScriptApp, SimpleScriptProcessor
except ModuleNotFoundError:
    # Allow running as a standalone script without package context.
    from pathlib import Path as _Path
    import sys as _sys

    root = _Path(__file__).resolve().parent
    _sys.path.append(str(root))
    from _shared import SimpleScriptApp, SimpleScriptProcessor  # type: ignore


@dataclass
class VSMEntry:
    path: Path
    sample: str
    dataframe: pd.DataFrame  # columns: temperature, field, signal


@dataclass
class PlotSeries:
    field: float
    direction: str
    segment_index: int
    frame: pd.DataFrame


class VSMTemperatureScanProcessor(SimpleScriptProcessor):
    """Processor powering the Tk UI."""

    def __init__(self) -> None:
        super().__init__()
        self.split_directions: bool = True
        self.show_derivative: bool = False
        self.show_smoothed_plot: bool = False
        self.median_window: int = 5
        self.moving_avg_window: int = 20
        self._prefs_path = Path.home() / ".vsm_temp_scan_prefs.json"
        self._load_prefs()

    def _load_prefs(self) -> None:
        try:
            content = json.loads(self._prefs_path.read_text())
            self.median_window = int(content.get("median_window", self.median_window))
            self.moving_avg_window = int(content.get("moving_avg_window", self.moving_avg_window))
            self.show_smoothed_plot = bool(content.get("show_smoothed_plot", self.show_smoothed_plot))
        except Exception:
            return

    def _save_prefs(self) -> None:
        data = {
            "median_window": self.median_window,
            "moving_avg_window": self.moving_avg_window,
            "show_smoothed_plot": self.show_smoothed_plot,
        }
        try:
            self._prefs_path.write_text(json.dumps(data))
        except Exception:
            pass

    def load(self, paths: list[Path]) -> list[VSMEntry]:
        entries: list[VSMEntry] = []
        for path in paths:
            data, sample = self._parse_file(path)
            if data.empty:
                self.log(f"{path.name}: no measurement rows found.")
                continue
            entries.append(VSMEntry(path=path, sample=sample, dataframe=data))
        if not entries:
            raise RuntimeError("No usable VSM temperature scan data was found.")
        self.log(f"Loaded {len(entries)} dataset(s).")
        return entries

    # ------------------------------------------------------------------ parsing helpers
    def _parse_file(self, path: Path) -> tuple[pd.DataFrame, str]:
        sample_name = path.stem
        columns: list[str] = []
        data_rows: list[dict[str, float]] = []
        current_section: int | None = None
        section_index = -1
        seen_sections: list[int] = []
        header_finished = False
        stop_reading = False
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                if stop_reading:
                    break
                line = raw_line.strip()
                if not header_finished:
                    if line.startswith("@Samplename:"):
                        sample_name = line.split(":", 1)[1].strip() or sample_name
                    if line.startswith("@@End of Header."):
                        header_finished = True
                    continue
                if not line or line.startswith("@@Data") or line.startswith("New Section"):
                    if line.startswith("New Section"):
                        try:
                            token = line.split("Section", 1)[1]
                            parsed_section = int("".join(ch for ch in token if ch.isdigit()))
                        except Exception:
                            parsed_section = section_index + 1
                        if parsed_section not in seen_sections and len(seen_sections) < 4:
                            seen_sections.append(parsed_section)
                        elif parsed_section in seen_sections and len(seen_sections) >= 4:
                            stop_reading = True
                            continue
                        current_section = parsed_section
                        section_index = int(seen_sections.index(parsed_section))
                    continue
                if line.startswith("@"):
                    continue
                if line.startswith("Time_since_start"):
                    columns = [token.strip() for token in line.split()]
                    continue
                parts = [token for token in line.split() if token]
                if columns and len(parts) >= len(columns):
                    row = dict(zip(columns, parts))
                    temperature = self._pick_float(
                        row,
                        [
                            "Sample_Temperature_For_Plot_",
                            "Raw_Sample_Temperature_For_Plot_",
                            "Temperature",
                            "Raw_Temperature",
                        ],
                    )
                    field = self._pick_float(row, ["Applied_Field", "Raw_Applied_Field"])
                    signal = self._pick_float(
                        row,
                        [
                            "Signal_X_direction",
                            "Raw_Signal_Mx",
                        ],
                    )
                    if temperature is None or field is None or signal is None:
                        continue
                    if current_section is None or section_index < 0:
                        continue
                    if current_section not in seen_sections[:4]:
                        continue
                    data_rows.append(
                        {
                            "temperature": temperature,
                            "field": field,
                            "signal": signal,
                            "section": current_section,
                            "section_index": int(section_index),
                        }
                    )
        frame = pd.DataFrame.from_records(data_rows)
        return frame, sample_name

    def _pick_float(self, mapping: Dict[str, str], keys: Sequence[str]) -> float | None:
        for key in keys:
            if key in mapping:
                try:
                    return float(mapping[key])
                except Exception:
                    continue
        return None

    def _to_float(self, value: Any) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    def _smooth_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Apply median and moving-average smoothing to the signal column."""

        smoothed = frame.sort_values("temperature").drop_duplicates(subset="temperature").copy()
        signal = smoothed["signal"].astype(float)
        median = signal.rolling(
            window=max(1, int(self.median_window)), center=True, min_periods=1
        ).median()
        ma = median.rolling(
            window=max(1, int(self.moving_avg_window)), center=True, min_periods=1
        ).mean()
        smoothed["signal"] = ma.ffill().bfill()
        return smoothed

    def _build_series(self, frame: pd.DataFrame) -> list[PlotSeries]:
        series: list[PlotSeries] = []
        if frame.empty:
            return series
        has_sections = "section_index" in frame.columns
        for field_value, subset in frame.groupby("field"):
            field_float = self._to_float(field_value)
            if field_float is None:
                continue
            ordered_subset = subset.reset_index(drop=True)
            if has_sections and self.split_directions:
                for section_idx, segment in ordered_subset.groupby("section_index"):
                    segment_raw = segment.reset_index(drop=True)
                    temps_raw = segment_raw["temperature"].astype(float)
                    direction = "flat"
                    if len(temps_raw) >= 2:
                        delta = temps_raw.iloc[-1] - temps_raw.iloc[0]
                        direction = "up" if delta > 0 else "down" if delta < 0 else "flat"
                    segment_sorted, removed = self._dedupe_temperatures(segment_raw)
                    if removed:
                        self.log(
                            f"Removed {removed} duplicate temperature point(s) for {field_float:.0f} Oe section {int(section_idx) + 1}."
                        )
                    series.append(
                        PlotSeries(field_float, direction, int(section_idx), segment_sorted)
                    )
            elif self.split_directions:
                direction_counts: dict[str, int] = {}
                for direction, segment in self._split_segments(ordered_subset):
                    direction_counts[direction] = direction_counts.get(direction, 0) + 1
                    series.append(
                        PlotSeries(field_float, direction, direction_counts[direction], segment)
                    )
            else:
                sorted_subset = ordered_subset.sort_values("temperature")
                series.append(PlotSeries(field_float, "all", 1, sorted_subset))
        series.sort(key=lambda item: (item.field, item.segment_index), reverse=True)
        return series

    def _dedupe_temperatures(self, frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        sorted_frame = frame.sort_values("temperature").reset_index(drop=True)
        if sorted_frame.empty:
            return sorted_frame, 0
        grouped = (
            sorted_frame.groupby("temperature", as_index=False)
            .agg(
                {
                    "signal": "mean",
                    "field": "first",
                    "section": "first" if "section" in sorted_frame.columns else "first",
                    "section_index": "first" if "section_index" in sorted_frame.columns else "first",
                }
            )
        )
        # Preserve any other columns by first value to avoid losing metadata.
        for column in sorted_frame.columns:
            if column in grouped.columns:
                continue
            grouped[column] = sorted_frame.groupby("temperature")[column].first().values
        removed = int(len(sorted_frame) - len(grouped))
        return grouped.reset_index(drop=True), removed

    def _split_segments(self, frame: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
        if len(frame) < 2:
            return [("all", frame.reset_index(drop=True))]
        temps = frame["temperature"].astype(float).to_numpy()
        span = float(np.nanmax(temps) - np.nanmin(temps)) if len(temps) else 0.0
        threshold = max(0.05, 0.002 * span)
        deltas = np.diff(temps)
        signs: list[int] = []
        for delta in deltas:
            if abs(delta) <= threshold:
                signs.append(0)
            elif delta > 0:
                signs.append(1)
            else:
                signs.append(-1)

        segments: list[tuple[str, pd.DataFrame]] = []
        start = 0
        current_dir: int | None = None
        pending_dir: int | None = None
        pending_len = 0
        min_persist = 3

        def _dir_label(sign: int | None) -> str:
            if sign is None:
                return "flat"
            return "up" if sign > 0 else "down"

        for idx, sign in enumerate(signs, start=1):
            if sign == 0:
                continue
            if current_dir is None:
                current_dir = sign
                continue
            if sign == current_dir:
                pending_dir = None
                pending_len = 0
                continue
            if sign != pending_dir:
                pending_dir = sign
                pending_len = 1
                continue
            pending_len += 1
            if pending_len >= min_persist:
                split_at = idx - min_persist + 1
                segments.append(
                    (_dir_label(current_dir), frame.iloc[start:split_at].copy().reset_index(drop=True))
                )
                start = split_at
                current_dir = sign
                pending_dir = None
                pending_len = 0

        segments.append(
            (_dir_label(current_dir), frame.iloc[start:].copy().reset_index(drop=True))
        )
        # Merge back-to-back segments with same label that may have slipped through tolerance
        merged: list[tuple[str, pd.DataFrame]] = []
        for direction, segment in segments:
            if merged and merged[-1][0] == direction:
                combined = pd.concat([merged[-1][1], segment], ignore_index=True)
                merged[-1] = (direction, combined)
            else:
                merged.append((direction, segment))
        return merged

    def _direction_suffix(self, direction: str, segment: int = 1) -> str:
        if direction in {"all", "flat"}:
            return ""
        suffix = f"_{direction}"
        if segment > 1:
            suffix += f"_{segment}"
        return suffix

    def _direction_label(self, direction: str, segment: int = 1, *, section: int | None = None) -> str:
        base = ""
        if direction == "up":
            base = " ↑ heating"
        elif direction == "down":
            base = " ↓ cooling"
        elif direction == "flat":
            base = " (flat)"
        if section is not None:
            base = f"{base} S{section + 1}"
        return base

    def _compute_derivative(self, frame: pd.DataFrame) -> list[float]:
        temps = frame["temperature"].astype(float).to_numpy()
        signals = frame["signal"].astype(float).to_numpy()
        if len(temps) < 2:
            return [0.0 for _ in temps]
        delta_t = np.diff(temps)
        delta_s = np.diff(signals)
        derivative = np.zeros_like(signals, dtype=float)
        nonzero = np.abs(delta_t) > 1e-9
        derivative[1:] = np.divide(
            delta_s,
            delta_t,
            out=np.zeros_like(delta_s, dtype=float),
            where=nonzero,
        )
        derivative[0] = derivative[1] if len(derivative) > 1 else 0.0
        return derivative.tolist()

    def set_split_directions(self, enabled: bool) -> None:
        self.split_directions = bool(enabled)

    def set_show_derivative(self, enabled: bool) -> None:
        self.show_derivative = bool(enabled)
        self._save_prefs()

    def set_show_smoothed(self, enabled: bool) -> None:
        self.show_smoothed_plot = bool(enabled)
        self._save_prefs()

    def set_smoothing_windows(self, median: int, moving_avg: int) -> None:
        try:
            self.median_window = max(1, int(median))
        except Exception:
            pass
        try:
            self.moving_avg_window = max(1, int(moving_avg))
        except Exception:
            pass
        self._save_prefs()

    # ------------------------------------------------------------------ Matplotlib plotting
    def plot_matplotlib(self, dataset: list[VSMEntry]) -> None:
        if plt is None:  # pragma: no cover - Matplotlib optional
            raise RuntimeError("matplotlib is not installed in this environment.")
        for entry in dataset:
            series = self._build_series(entry.dataframe.copy())
            if not series:
                continue
            fig, ax_left = plt.subplots(figsize=(9, 5))
            ax_left.set_title(f"{entry.sample} - VSM Temperature Scan")
            ax_left.set_xlabel("Temperature (°C)")
            ax_left.set_ylabel("Signal X (emu)")
            colors = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0ea5e9"]
            handles: list[Any] = []
            field_axes: Dict[float, Any] = {}
            secondary_map: Dict[Any, float] = {}
            ax_right = None
            deriv_handles: list[Any] = []
            fig_deriv = None
            ax_deriv = None
            if self.show_derivative:
                fig_deriv, ax_deriv = plt.subplots(figsize=(9, 5))
                ax_deriv.set_title(f"{entry.sample} - d(Signal X)/dT")
                ax_deriv.set_xlabel("Temperature (°C)")
                ax_deriv.set_ylabel("dS/dT (emu/°C)")

            def pick_axis(field: float) -> Any:
                nonlocal ax_right
                if field in field_axes:
                    return field_axes[field]
                if not field_axes:
                    field_axes[field] = ax_left
                    return ax_left
                if ax_right is None:
                    ax_right = ax_left.twinx()
                field_axes[field] = ax_right
                secondary_map[ax_right] = field
                return field_axes[field]

            for idx, entry_series in enumerate(series):
                axis = pick_axis(entry_series.field)
                smoothed_frame = self._smooth_frame(entry_series.frame)
                color = colors[idx % len(colors)]
                label = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)}"
                line = axis.plot(
                    entry_series.frame["temperature"],
                    entry_series.frame["signal"],
                    color=color,
                    label=label,
                    linewidth=1.6,
                )[0]
                handles.append(line)
                if self.show_derivative and ax_deriv is not None:
                    derivative = self._compute_derivative(smoothed_frame)
                    dlabel = f"d/dT {label}"
                    dline = ax_deriv.plot(
                        smoothed_frame["temperature"],
                        derivative,
                        color=color,
                        linestyle="--",
                        linewidth=1.2,
                        label=dlabel,
                    )[0]
                    deriv_handles.append(dline)

            if handles:
                ax_left.legend(handles=handles, loc="best")
            if ax_right is not None and secondary_map:
                secondary_label = ", ".join(f"{val:.0f} Oe" for val in sorted(set(secondary_map.values())))
                try:
                    ax_right.set_ylabel(f"Signal X (emu) – {secondary_label}", color="#111827")
                    ax_right.tick_params(axis="y", colors="#111827")
                except Exception:
                    pass
            fig.tight_layout()

            if fig_deriv is not None and ax_deriv is not None and deriv_handles:
                ax_deriv.legend(handles=deriv_handles, loc="best")
                fig_deriv.tight_layout()

            if self.show_smoothed_plot:
                fig_s, ax_s = plt.subplots(figsize=(9, 5))
                ax_s.set_title(f"{entry.sample} - Smoothed Signal X")
                ax_s.set_xlabel("Temperature (°C)")
                ax_s.set_ylabel("Signal X (emu)")
                smooth_handles: list[Any] = []
                for idx, entry_series in enumerate(series):
                    smoothed_frame = self._smooth_frame(entry_series.frame)
                    color = colors[idx % len(colors)]
                    label = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)}"
                    handle = ax_s.plot(
                        smoothed_frame["temperature"],
                        smoothed_frame["signal"],
                        color=color,
                        label=label,
                        linewidth=1.6,
                    )[0]
                    smooth_handles.append(handle)
                if smooth_handles:
                    ax_s.legend(handles=smooth_handles, loc="best")
                fig_s.tight_layout()

            try:
                plt.show()
            except Exception:
                plt.show(block=True)
            self.log(f"Displayed Matplotlib plot for {entry.sample}.")

    # ------------------------------------------------------------------ Origin plotting
    def plot_origin(self, dataset: list[VSMEntry]) -> None:
        if op is None:  # pragma: no cover - requires Origin
            raise RuntimeError(
                "originpro is not available. Install OriginLab's Python package on this machine."
            )
        try:
            op.set_show(True)
        except Exception:
            pass
        plotted = 0
        deriv_plotted = 0
        deriv_book = None
        smooth_book = None
        for entry in dataset:
            series = self._build_series(entry.dataframe.copy())
            if not series:
                continue
            book = op.new_book("w")
            book.lname = f"{entry.sample} (TScan)"
            wks = cast(Any, book[0])
            wks.name = "Data"
            col_index = 0
            column_pairs: list[tuple[float, str, int, str]] = []
            for entry_series in series:
                frame, _ = self._dedupe_temperatures(entry_series.frame)
                frame = frame.sort_values("temperature")
                temps = frame["temperature"].tolist()
                signals = frame["signal"].tolist()
                wks.from_list(col_index, temps)
                col_x = cast(Any, wks.obj.Columns(col_index))
                col_x.LongName = "Temperature"
                col_x.Units = "°C"
                col_x.Comment = f"Section {entry_series.segment_index + 1}"
                col_x.Type = 3
                wks.from_list(col_index + 1, signals)
                col_y = cast(Any, wks.obj.Columns(col_index + 1))
                suffix = self._direction_label(entry_series.direction, entry_series.segment_index)
                col_y.LongName = "Signal X"
                col_y.Units = "emu"
                col_y.Comment = f"{entry_series.field:.0f} Oe{suffix} (Section {entry_series.segment_index + 1})"
                col_y.Type = 4
                try:
                    wks.cols_axis("XY")
                except Exception:
                    pass
                column_pairs.append((entry_series.field, entry_series.direction, col_index, col_y.Comment))
                col_index += 2

            graph = op.new_graph(template="doubley")
            try:
                graph.set_str("title", f"{entry.sample} - VSM Temperature Scan")
            except Exception:
                pass
            unique_fields: list[float] = []
            for field_value, _, _ in column_pairs:
                if field_value not in unique_fields:
                    unique_fields.append(field_value)
            layer_map: Dict[float, Any] = {}
            existing_layers = len(graph)
            for idx, field_value in enumerate(unique_fields):
                if idx == 0:
                    layer = graph[0]
                elif idx == 1 and existing_layers > 1:
                    layer = graph[1]
                else:
                    layer = graph.add_layer()
                layer_map[field_value] = layer
                try:
                    layer.axis(0).title = "Temperature (°C)"
                    layer.axis(1).title = "Signal X (emu)"
                except Exception:
                    pass
            for field_value, direction, base_col, legend_text in column_pairs:
                layer = layer_map.get(field_value, graph[0])
                plot_obj = cast(Any, layer.add_plot(wks, coly=base_col + 1, colx=base_col))
                if plot_obj is not None:
                    try:
                        plot_obj.legend = legend_text
                    except Exception:
                        pass
                try:
                    layer.rescale()
                except Exception:
                    pass
            try:
                graph.activate()
                op.lt_exec("doc -tf;")
            except Exception:
                pass
            plotted += 1
            self.log(f"Sent {entry.sample} to Origin.")

            if self.show_smoothed_plot:
                if smooth_book is None:
                    smooth_book = op.new_book("w")
                    smooth_book.lname = f"{entry.sample} (TScan Smoothed)"
                smooth_sheet = smooth_book.add_sheet()
                smooth_sheet.name = f"{entry.sample}"
                scol = 0
                smooth_pairs: list[tuple[float, str, int, str]] = []
                for entry_series in series:
                    base_frame, _ = self._dedupe_temperatures(entry_series.frame)
                    frame = self._smooth_frame(base_frame.sort_values("temperature"))
                    temps = frame["temperature"].tolist()
                    signals = frame["signal"].tolist()
                    smooth_sheet.from_list(scol, temps)
                    col_x = cast(Any, smooth_sheet.obj.Columns(scol))
                    col_x.LongName = "Temperature"
                    col_x.Units = "°C"
                    col_x.Comment = f"Section {entry_series.segment_index + 1}"
                    col_x.Type = 3
                    smooth_sheet.from_list(scol + 1, signals)
                    col_y = cast(Any, smooth_sheet.obj.Columns(scol + 1))
                    suffix = self._direction_label(entry_series.direction, entry_series.segment_index)
                    col_y.LongName = "Signal X (smoothed)"
                    col_y.Units = "emu"
                    col_y.Comment = f"{entry_series.field:.0f} Oe{suffix} (Section {entry_series.segment_index + 1})"
                    col_y.Type = 4
                    smooth_legends.append(col_y.Comment)
                    try:
                        smooth_sheet.cols_axis("XY")
                    except Exception:
                        pass
                    smooth_pairs.append((entry_series.field, entry_series.direction, scol, col_y.Comment))
                    scol += 2
                smooth_graph = op.new_graph(template="doubley")
                try:
                    smooth_graph.set_str("title", f"{entry.sample} - Smoothed Signal X")
                except Exception:
                    pass
                s_unique: list[float] = []
                for field_value, _, _, _ in smooth_pairs:
                    if field_value not in s_unique:
                        s_unique.append(field_value)
                s_layer_map: Dict[float, Any] = {}
                s_existing = len(smooth_graph)
                for idx, field_value in enumerate(s_unique):
                    if idx == 0:
                        layer = smooth_graph[0]
                    elif idx == 1 and s_existing > 1:
                        layer = smooth_graph[1]
                    else:
                        layer = smooth_graph.add_layer()
                    s_layer_map[field_value] = layer
                    try:
                        layer.axis(0).title = "Temperature (°C)"
                        layer.axis(1).title = "Signal X (emu)"
                    except Exception:
                        pass
                for field_value, direction, base_col, legend_text in smooth_pairs:
                    layer = s_layer_map.get(field_value, smooth_graph[0])
                    plot_obj = cast(Any, layer.add_plot(smooth_sheet, coly=base_col + 1, colx=base_col))
                    if plot_obj is not None:
                        try:
                            plot_obj.legend = legend_text
                        except Exception:
                            pass
                    try:
                        layer.rescale()
                    except Exception:
                        pass
                try:
                    smooth_graph.activate()
                    op.lt_exec("doc -tf;")
                except Exception:
                    pass

            if self.show_derivative:
                if deriv_book is None:
                    deriv_book = op.new_book("w")
                    deriv_book.lname = f"{entry.sample} (TScan Derivatives)"
                deriv_sheet = deriv_book.add_sheet()
                deriv_sheet.name = f"{entry.sample}"
                col = 0
                derivative_column_pairs: list[tuple[int, str, int, str]] = []
                for entry_series in series:
                    base_frame, _ = self._dedupe_temperatures(entry_series.frame)
                    frame = self._smooth_frame(base_frame.sort_values("temperature"))
                    temps = frame["temperature"].tolist()
                    derivs = self._compute_derivative(frame)
                    deriv_sheet.from_list(col, temps)
                    col_x = cast(Any, deriv_sheet.obj.Columns(col))
                    col_x.LongName = "Temperature"
                    col_x.Units = "°C"
                    col_x.Comment = f"Section {entry_series.segment_index + 1}"
                    col_x.Type = 3
                    deriv_sheet.from_list(col + 1, derivs)
                    col_y = cast(Any, deriv_sheet.obj.Columns(col + 1))
                    col_y.LongName = "dSignal/dT"
                    col_y.Units = "emu/°C"
                    col_y.Comment = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)} (Section {entry_series.segment_index + 1})"
                    col_y.Type = 4
                    try:
                        deriv_sheet.cols_axis("XY")
                    except Exception:
                        pass
                    col += 2
                    derivative_column_pairs.append((entry_series.field, entry_series.direction, col - 2, col_y.Comment))
                deriv_plotted += 1
                # Plot derivative graph for this book
                deriv_graph = op.new_graph()
                try:
                    deriv_graph.set_str("title", f"{entry.sample} - d(Signal X)/dT")
                except Exception:
                    pass
                layer = deriv_graph[0]
                try:
                    layer.axis(0).title = "Temperature (°C)"
                    layer.axis(1).title = "d(Signal X)/dT (emu/°C)"
                except Exception:
                    pass
                for field_value, direction, base_col, legend_text in derivative_column_pairs:
                    plot_obj = cast(Any, layer.add_plot(deriv_sheet, coly=base_col + 1, colx=base_col))
                    if plot_obj is not None:
                        try:
                            plot_obj.legend = legend_text
                        except Exception:
                            pass
                    try:
                        layer.rescale()
                    except Exception:
                        pass
        if plotted == 0:
            raise RuntimeError("No data was available for Origin plotting.")
        if self.show_derivative and deriv_book is not None:
            try:
                deriv_book.activate()
                op.lt_exec("doc -tf;")
            except Exception:
                pass
        if self.show_smoothed_plot and smooth_book is not None:
            try:
                smooth_book.activate()
                op.lt_exec("doc -tf;")
            except Exception:
                pass

    # ------------------------------------------------------------------ TXT export
    def export_txt(self, dataset: list[VSMEntry], output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for entry in dataset:
            series = self._build_series(entry.dataframe.copy())
            if not series:
                continue
            # Main data export (one file per graph)
            fname = output_dir / f"{entry.path.stem}_TScan.txt"
            headers: List[List[str]] = []
            columns: List[List[str]] = []
            for entry_series in series:
                frame, _ = self._dedupe_temperatures(entry_series.frame)
                frame = frame.sort_values("temperature")
                temps = [f"{val:.6f}" for val in frame["temperature"].tolist()]
                signals = [f"{val:.6e}" for val in frame["signal"].tolist()]
                columns.append(temps)
                columns.append(signals)
                headers.append(
                    [
                        "Temperature",
                        "Signal X",
                    ]
                )
                headers.append(
                    [
                        "°C",
                        "emu",
                    ]
                )
                legend = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)} (Section {entry_series.segment_index + 1})"
                headers.append(
                    [
                        f"Section {entry_series.segment_index + 1}",
                        legend,
                    ]
                )
            with fname.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle, delimiter="\t")
                # Write headers for each pair
                if headers:
                    for header_row in zip_longest(*headers, fillvalue=""):
                        writer.writerow(header_row)
                for data_row in zip_longest(*columns, fillvalue=""):
                    writer.writerow(data_row)
            self.log(f"Exported {fname.name}")

            # Derivative export (one file per graph) when enabled
            if self.show_derivative:
                dname = output_dir / f"{entry.path.stem}_TScan_derivative.txt"
                d_headers: List[List[str]] = []
                d_columns: List[List[str]] = []
                for entry_series in series:
                    base_frame, _ = self._dedupe_temperatures(entry_series.frame)
                    frame = self._smooth_frame(base_frame.sort_values("temperature"))
                    temps = [f"{val:.6f}" for val in frame["temperature"].tolist()]
                    derivs = [f"{val:.6e}" for val in self._compute_derivative(frame)]
                    d_columns.append(temps)
                    d_columns.append(derivs)
                    d_headers.append(["Temperature", "dSignal/dT"])
                    d_headers.append(["°C", "emu/°C"])
                    legend = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)} (Section {entry_series.segment_index + 1})"
                    d_headers.append([f"Section {entry_series.segment_index + 1}", legend])
                with dname.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle, delimiter="\t")
                    if d_headers:
                        for header_row in zip_longest(*d_headers, fillvalue=""):
                            writer.writerow(header_row)
                    for data_row in zip_longest(*d_columns, fillvalue=""):
                        writer.writerow(data_row)
                self.log(f"Exported {dname.name}")

            if self.show_smoothed_plot:
                sname = output_dir / f"{entry.path.stem}_TScan_smoothed.txt"
                s_headers: List[List[str]] = []
                s_columns: List[List[str]] = []
                for entry_series in series:
                    base_frame, _ = self._dedupe_temperatures(entry_series.frame)
                    frame = self._smooth_frame(base_frame.sort_values("temperature"))
                    temps = [f"{val:.6f}" for val in frame["temperature"].tolist()]
                    signals = [f"{val:.6e}" for val in frame["signal"].tolist()]
                    s_columns.append(temps)
                    s_columns.append(signals)
                    s_headers.append(["Temperature", "Signal X (smoothed)"])
                    s_headers.append(["°C", "emu"])
                    legend = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)} (Section {entry_series.segment_index + 1})"
                    s_headers.append([f"Section {entry_series.segment_index + 1}", legend])
                with sname.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle, delimiter="\t")
                    if s_headers:
                        for header_row in zip_longest(*s_headers, fillvalue=""):
                            writer.writerow(header_row)
                    for data_row in zip_longest(*s_columns, fillvalue=""):
                        writer.writerow(data_row)
                self.log(f"Exported {sname.name}")
        self.log(f"TXT export complete: {output_dir}")


def main() -> None:
    processor = VSMTemperatureScanProcessor()
    app = SimpleScriptApp("VSM Temperature Scan", processor)
    split_var = tk.BooleanVar(app, value=True)
    processor.set_split_directions(True)
    ttk.Checkbutton(
        app.options_frame,
        text="Separate heating/cooling",
        variable=split_var,
        command=lambda: processor.set_split_directions(split_var.get()),
    ).pack(side=tk.LEFT, padx=(0, 12))

    deriv_var = tk.BooleanVar(app, value=False)
    ttk.Checkbutton(
        app.options_frame,
        text="Plot derivatives",
        variable=deriv_var,
        command=lambda: processor.set_show_derivative(deriv_var.get()),
    ).pack(side=tk.LEFT)

    smooth_plot_var = tk.BooleanVar(app, value=processor.show_smoothed_plot)
    ttk.Checkbutton(
        app.options_frame,
        text="Show smoothed plot",
        variable=smooth_plot_var,
        command=lambda: processor.set_show_smoothed(smooth_plot_var.get()),
    ).pack(side=tk.LEFT, padx=(12, 0))

    med_label = ttk.Label(app.options_frame, text="Median window:")
    med_label.pack(side=tk.LEFT, padx=(12, 4))
    med_entry = ttk.Entry(app.options_frame, width=4)
    med_entry.insert(0, str(processor.median_window))
    med_entry.pack(side=tk.LEFT)
    ma_label = ttk.Label(app.options_frame, text="MA window:")
    ma_label.pack(side=tk.LEFT, padx=(4, 4))
    ma_entry = ttk.Entry(app.options_frame, width=4)
    ma_entry.insert(0, str(processor.moving_avg_window))
    ma_entry.pack(side=tk.LEFT)

    def _apply_smoothing() -> None:
        try:
            median = int(med_entry.get())
            ma = int(ma_entry.get())
        except Exception:
            median = processor.median_window
            ma = processor.moving_avg_window
        processor.set_smoothing_windows(median, ma)

    ttk.Button(
        app.options_frame,
        text="Apply smoothing",
        command=_apply_smoothing,
    ).pack(side=tk.LEFT, padx=(6, 0))
    app.mainloop()


if __name__ == "__main__":
    main()
