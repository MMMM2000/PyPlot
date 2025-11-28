#!/usr/bin/env python3
"""Tkinter UI for plotting VSM temperature scan data (Signal X vs Temperature)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple, cast

import pandas as pd
try:
    import tkinter as tk  # noqa: F401 - required for Tk dialogs used in _shared
    from tkinter import ttk
except Exception:  # pragma: no cover - allow headless/plugin use without Tk
    tk = None  # type: ignore
    ttk = None  # type: ignore
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


VSM_TEMP_SCAN_COLORS = ["#dc2626", "#2563eb", "#f97316", "#16a34a"]


class VSMTemperatureScanProcessor(SimpleScriptProcessor):
    """Processor powering the Tk UI."""

    def __init__(self) -> None:
        super().__init__()
        self.split_directions: bool = True
        self.show_derivative: bool = False
        self.show_smoothed_derivative: bool = False
        self.show_overlay_derivative: bool = False
        self.show_smoothed_plot: bool = False
        self.median_window: int = 5
        self.moving_avg_window: int = 201
        self.smooth_derivative: bool = True
        self.derivative_median_window: int = 5
        self.derivative_moving_avg_window: int = 201
        self._prefs_path = Path.home() / ".vsm_temp_scan_prefs.json"
        self._load_prefs()

    def _load_prefs(self) -> None:
        try:
            content = json.loads(self._prefs_path.read_text())
            self.median_window = int(content.get("median_window", self.median_window))
            self.moving_avg_window = int(content.get("moving_avg_window", self.moving_avg_window))
            self.show_smoothed_plot = bool(content.get("show_smoothed_plot", self.show_smoothed_plot))
            self.smooth_derivative = bool(content.get("smooth_derivative", self.smooth_derivative))
            self.show_smoothed_derivative = bool(content.get("show_smoothed_derivative", self.show_smoothed_derivative))
            self.show_overlay_derivative = bool(content.get("show_overlay_derivative", self.show_overlay_derivative))
            self.derivative_median_window = int(content.get("derivative_median_window", self.derivative_median_window))
            self.derivative_moving_avg_window = int(content.get("derivative_moving_avg_window", self.derivative_moving_avg_window))
        except Exception:
            return

    def _save_prefs(self) -> None:
        data = {
            "median_window": self.median_window,
            "moving_avg_window": self.moving_avg_window,
            "show_smoothed_plot": self.show_smoothed_plot,
            "smooth_derivative": self.smooth_derivative,
            "show_smoothed_derivative": self.show_smoothed_derivative,
            "show_overlay_derivative": self.show_overlay_derivative,
            "derivative_median_window": self.derivative_median_window,
            "derivative_moving_avg_window": self.derivative_moving_avg_window,
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
        smoothed["signal"] = self._smooth_values(
            signal,
            self.median_window,
            self.moving_avg_window,
        )
        return smoothed

    def _smooth_values(
        self,
        values: Sequence[float] | pd.Series,
        median_window: int,
        moving_avg_window: int,
    ) -> pd.Series:
        series = pd.Series(values, dtype=float)
        median = series.rolling(
            window=max(1, int(median_window)),
            center=True,
            min_periods=1,
        ).median()
        ma = median.rolling(
            window=max(1, int(moving_avg_window)),
            center=True,
            min_periods=1,
        ).mean()
        return ma.ffill().bfill()

    def series_color_map(self, series: Sequence[PlotSeries]) -> dict[tuple[float, str, int], str]:
        """Assign a stable color per (field, direction, segment) tuple."""

        return {
            (entry.field, entry.direction, entry.segment_index): VSM_TEMP_SCAN_COLORS[idx % len(VSM_TEMP_SCAN_COLORS)]
            for idx, entry in enumerate(series)
        }

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

        keys: list[str] = ["temperature"]
        for candidate in ("field", "section_index", "section"):
            if candidate in sorted_frame.columns:
                keys.append(candidate)

        agg: dict[str, str] = {"signal": "mean"}
        for column in sorted_frame.columns:
            if column in {"temperature", "signal"}:
                continue
            if column not in keys:
                agg[column] = "first"

        grouped = sorted_frame.groupby(keys, as_index=False).agg(agg)
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
            base = " ↑"
        elif direction == "down":
            base = " ↓"
        elif direction == "flat":
            base = " (flat)"
        if section is not None:
            base = f"{base} S{section + 1}"
        return base

    def _compute_derivative(self, frame: pd.DataFrame, *, smooth: bool | None = None) -> list[float]:
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
        result = derivative.tolist()
        should_smooth = self.smooth_derivative if smooth is None else bool(smooth)
        if should_smooth:
            result = self._smooth_values(
                result,
                self.derivative_median_window,
                self.derivative_moving_avg_window,
            ).tolist()
        return result

    def set_split_directions(self, enabled: bool) -> None:
        self.split_directions = bool(enabled)

    def set_show_derivative(self, enabled: bool) -> None:
        self.show_derivative = bool(enabled)
        self._save_prefs()

    def set_show_smoothed_derivative(self, enabled: bool) -> None:
        self.show_smoothed_derivative = bool(enabled)
        if enabled and not self.smooth_derivative:
            self.smooth_derivative = True
        self._save_prefs()

    def set_show_overlay_derivative(self, enabled: bool) -> None:
        self.show_overlay_derivative = bool(enabled)
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

    def set_derivative_smoothing_windows(self, median: int, moving_avg: int) -> None:
        try:
            self.derivative_median_window = max(1, int(median))
        except Exception:
            pass
        try:
            self.derivative_moving_avg_window = max(1, int(moving_avg))
        except Exception:
            pass
        self._save_prefs()

    def _origin_legend_label(self, layer: Any) -> Any | None:
        label_method = getattr(layer, "label", None)
        if not callable(label_method):
            return None
        try:
            legend = label_method("Legend")
        except Exception:
            legend = None
        if legend is None or not hasattr(legend, "text"):
            return None
        return cast(Any, legend)

    def _set_origin_legend(self, layer: Any, entries: list[tuple[int, str]]) -> None:
        legend = self._origin_legend_label(layer)
        if legend is None or not entries:
            return
        try:
            legend.text = "\n".join(f"\\l({index}) {label}" for index, label in entries)
        except Exception:
            pass

    # ------------------------------------------------------------------ Matplotlib plotting
    def plot_matplotlib(self, dataset: list[VSMEntry]) -> None:
        if plt is None:  # pragma: no cover - Matplotlib optional
            raise RuntimeError("matplotlib is not installed in this environment.")
        for entry in dataset:
            series = self._build_series(entry.dataframe.copy())
            if not series:
                continue
            color_map = self.series_color_map(series)
            fig, ax_left = plt.subplots(figsize=(9, 5))
            ax_left.set_title(f"{entry.sample} - VSM Temperature Scan")
            ax_left.set_xlabel("Temperature (°C)")
            ax_left.set_ylabel("Signal X (emu)")
            handles: list[Any] = []
            field_axes: Dict[float, Any] = {}
            secondary_map: Dict[Any, float] = {}
            ax_right = None
            deriv_handles: list[Any] = []
            fig_deriv = None
            ax_deriv = None
            fig_deriv_s = None
            ax_deriv_s = None
            if self.show_derivative:
                fig_deriv, ax_deriv = plt.subplots(figsize=(9, 5))
                ax_deriv.set_title(f"{entry.sample} - d(Signal X)/dT")
                ax_deriv.set_xlabel("Temperature (°C)")
                ax_deriv.set_ylabel("dS/dT (emu/°C)")
            if self.show_smoothed_derivative:
                fig_deriv_s, ax_deriv_s = plt.subplots(figsize=(9, 5))
                ax_deriv_s.set_title(f"{entry.sample} - Smoothed d(Signal X)/dT")
                ax_deriv_s.set_xlabel("Temperature (°C)")
                ax_deriv_s.set_ylabel("dS/dT (emu/°C)")

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
                color_key = (entry_series.field, entry_series.direction, entry_series.segment_index)
                color = color_map.get(color_key, VSM_TEMP_SCAN_COLORS[idx % len(VSM_TEMP_SCAN_COLORS)])
                label = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)}"
                line = axis.plot(
                    entry_series.frame["temperature"],
                    entry_series.frame["signal"],
                    color=color,
                    label=label,
                    linewidth=1.6,
                )[0]
                handles.append(line)
                if (self.show_derivative and ax_deriv is not None) or (self.show_smoothed_derivative and ax_deriv_s is not None):
                    derivative_raw = self._compute_derivative(smoothed_frame, smooth=False)
                    derivative_smoothed = self._compute_derivative(smoothed_frame, smooth=True)
                    dlabel = f"d/dT {label}"
                    if self.show_derivative and ax_deriv is not None:
                        dline = ax_deriv.plot(
                            smoothed_frame["temperature"],
                            derivative_raw,
                            color=color,
                            linestyle="--",
                            linewidth=1.2,
                            label=dlabel,
                        )[0]
                        deriv_handles.append(dline)
                    if self.smooth_derivative and ax_deriv_s is not None:
                        ax_deriv_s.plot(
                            smoothed_frame["temperature"],
                            derivative_smoothed,
                            color=color,
                            linestyle="-",
                            linewidth=1.2,
                            label=dlabel,
                        )

            if handles:
                legend_handles: list[Any] = []
                legend_labels: list[str] = []
                seen: set[str] = set()
                for handle in handles:
                    label = handle.get_label()
                    if not label or label in seen:
                        continue
                    seen.add(label)
                    legend_handles.append(handle)
                    legend_labels.append(label)
                if legend_handles:
                    legend_obj = ax_left.legend(handles=legend_handles, labels=legend_labels, loc="best")
                    try:
                        for text in legend_obj.get_texts():
                            text.set_color("#e5e7eb")
                        legend_obj.get_frame().set_edgecolor("#6b7280")
                    except Exception:
                        pass
            try:
                left_fields = [val for val, axis in field_axes.items() if axis is ax_left]
                if left_fields:
                    left_label_fields = ", ".join(f"{val:.0f} Oe" for val in sorted(set(left_fields)))
                    ax_left.set_ylabel(f"Signal X (emu) – {left_label_fields}", color="#e5e7eb")
            except Exception:
                pass
            if ax_right is not None and secondary_map:
                secondary_label = ", ".join(f"{val:.0f} Oe" for val in sorted(set(secondary_map.values())))
                try:
                    right_color = None
                    for key, axis in field_axes.items():
                        if axis is not ax_right:
                            continue
                        for entry_series in series:
                            if entry_series.field == key:
                                color_key = (
                                    entry_series.field,
                                    entry_series.direction,
                                    entry_series.segment_index,
                                )
                                right_color = color_map.get(color_key, "#cbd5e1")
                                break
                        if right_color:
                            break
                    ax_right.set_ylabel(f"Signal X (emu) – {secondary_label}", color=right_color or "#cbd5e1")
                    ax_right.tick_params(axis="y", colors=right_color or "#cbd5e1")
                except Exception:
                    pass
            try:
                ax_left.tick_params(axis="y", colors="#e5e7eb")
                ax_left.yaxis.label.set_color("#e5e7eb")
                ax_left.xaxis.label.set_color("#e5e7eb")
                ax_left.tick_params(axis="x", colors="#e5e7eb")
            except Exception:
                pass
            fig.subplots_adjust(left=0.12, right=0.86, bottom=0.12, top=0.92)

            if fig_deriv is not None and ax_deriv is not None and deriv_handles:
                legend_obj = ax_deriv.legend(loc="best")
                try:
                    for text in legend_obj.get_texts():
                        text.set_color("#e5e7eb")
                    legend_obj.get_frame().set_edgecolor("#6b7280")
                except Exception:
                    pass
                try:
                    ax_deriv.tick_params(axis="both", colors="#e5e7eb")
                    ax_deriv.xaxis.label.set_color("#e5e7eb")
                    ax_deriv.yaxis.label.set_color("#e5e7eb")
                except Exception:
                    pass
                fig_deriv.tight_layout()
            if fig_deriv_s is not None and ax_deriv_s is not None and self.smooth_derivative:
                legend_obj = ax_deriv_s.legend(loc="best")
                try:
                    for text in legend_obj.get_texts():
                        text.set_color("#e5e7eb")
                    legend_obj.get_frame().set_edgecolor("#6b7280")
                except Exception:
                    pass
                try:
                    ax_deriv_s.tick_params(axis="both", colors="#e5e7eb")
                    ax_deriv_s.xaxis.label.set_color("#e5e7eb")
                    ax_deriv_s.yaxis.label.set_color("#e5e7eb")
                except Exception:
                    pass
                fig_deriv_s.tight_layout()

            if self.show_smoothed_plot:
                fig_s, ax_s = plt.subplots(figsize=(9, 5))
                ax_s.set_title(f"{entry.sample} - Smoothed Signal X")
                ax_s.set_xlabel("Temperature (°C)")
                ax_s.set_ylabel("Signal X (emu)")
                smooth_handles: list[Any] = []
                for idx, entry_series in enumerate(series):
                    smoothed_frame = self._smooth_frame(entry_series.frame)
                    color_key = (entry_series.field, entry_series.direction, entry_series.segment_index)
                    color = color_map.get(color_key, VSM_TEMP_SCAN_COLORS[idx % len(VSM_TEMP_SCAN_COLORS)])
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

            if self.show_overlay_derivative:
                for entry_series in series:
                    fig_o, ax_o_left = plt.subplots(figsize=(9, 5))
                    ax_o_left.set_title(
                        f"{entry.sample} - {entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)} overlay"
                    )
                    raw_frame, _ = self._dedupe_temperatures(entry_series.frame)
                    raw_frame = raw_frame.sort_values("temperature")
                    smoothed_frame = self._smooth_frame(raw_frame)
                    deriv = self._compute_derivative(smoothed_frame, smooth=True)
                    color_key = (entry_series.field, entry_series.direction, entry_series.segment_index)
                    base_color = color_map.get(color_key, VSM_TEMP_SCAN_COLORS[0])
                    raw_color = base_color
                    smooth_color = "#a855f7" if base_color == VSM_TEMP_SCAN_COLORS[0] else base_color
                    deriv_color = "#22c55e"
                    raw_line = ax_o_left.plot(
                        raw_frame["temperature"],
                        raw_frame["signal"],
                        color=raw_color,
                        linewidth=1.1,
                        alpha=0.6,
                        label="Raw",
                    )[0]
                    sm_line = ax_o_left.plot(
                        smoothed_frame["temperature"],
                        smoothed_frame["signal"],
                        color=smooth_color,
                        linewidth=1.6,
                        linestyle="--",
                        label="Smoothed",
                    )[0]
                    ax_o_left.set_xlabel("Temperature (°C)")
                    ax_o_left.set_ylabel("Signal X (emu)")
                    ax_o_right = ax_o_left.twinx()
                    d_line = ax_o_right.plot(
                        smoothed_frame["temperature"],
                        deriv,
                        color=deriv_color,
                        linewidth=1.2,
                        label="d(Signal X)/dT (smoothed)",
                    )[0]
                    ax_o_right.set_ylabel("d(Signal X)/dT (emu/°C)")
                    handles = [raw_line, sm_line, d_line]
                    labels = [
                        f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)} - Raw",
                        f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)} - Smoothed",
                        f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)} - d/dT (smoothed)",
                    ]
                    legend = ax_o_left.legend(handles, labels, loc="best")
                    try:
                        for text in legend.get_texts():
                            text.set_color("#e5e7eb")
                        legend.get_frame().set_edgecolor("#6b7280")
                    except Exception:
                        pass
                    try:
                        ax_o_left.tick_params(axis="both", colors="#e5e7eb")
                        ax_o_left.yaxis.label.set_color("#e5e7eb")
                        ax_o_left.xaxis.label.set_color("#e5e7eb")
                        ax_o_right.tick_params(axis="y", colors="#cbd5e1")
                        ax_o_right.yaxis.label.set_color("#cbd5e1")
                    except Exception:
                        pass
                    fig_o.tight_layout()

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

        def _style_origin_layer(layer: Any, title: str) -> None:
            """Apply consistent axes, disable speed mode, and mirror the title to the top X axis."""

            try:
                layer.rescale()
            except Exception:
                pass
            try:
                layer.set_int("use_speed_mode", 0)
                layer.set_int("speedmode", 0)
                layer.set_int("antialias", 1)
            except Exception:
                pass
            try:
                axis_top = layer.axis(2)
                axis_top.title = title
                # Hide top tick labels while keeping the title visible.
                setattr(axis_top, "show_labels", False)
                setattr(axis_top, "showLabels", False)
            except Exception:
                try:
                    layer.lt_exec(f"layer.x2.title$=\"{title}\"; layer.x2.showlabels=0;")
                except Exception:
                    pass

        for entry in dataset:
            series = self._build_series(entry.dataframe.copy())
            if not series:
                continue
            color_map = self.series_color_map(series)
            include_raw_derivative = bool(self.show_derivative)
            series_info: list[tuple[PlotSeries, str, str, str]] = []
            for idx, entry_series in enumerate(series):
                section_label = f"Section {entry_series.segment_index + 1}"
                legend_text = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)} ({section_label})"
                color_key = (entry_series.field, entry_series.direction, entry_series.segment_index)
                color = color_map.get(color_key, VSM_TEMP_SCAN_COLORS[idx % len(VSM_TEMP_SCAN_COLORS)])
                series_info.append((entry_series, section_label, legend_text, color))
            book = op.new_book("w")
            book.lname = f"{entry.sample} (TScan)"
            data_sheet = cast(Any, book[0])
            data_sheet.name = "Data"
            col_index = 0
            column_pairs: list[tuple[float, int, str, str]] = []
            for entry_series, section_label, legend_text, color in series_info:
                frame, _ = self._dedupe_temperatures(entry_series.frame)
                frame = frame.sort_values("temperature")
                temps = frame["temperature"].tolist()
                signals = frame["signal"].tolist()
                data_sheet.from_list(
                    col_index,
                    temps,
                    lname="Temperature",
                    units="°C",
                    comments=section_label,
                    axis="X",
                )
                col_x = cast(Any, data_sheet.obj.Columns(col_index))
                col_x.Type = 3
                data_sheet.from_list(
                    col_index + 1,
                    signals,
                    lname="Signal X",
                    units="emu",
                    comments=legend_text,
                    axis="Y",
                )
                col_y = cast(Any, data_sheet.obj.Columns(col_index + 1))
                col_y.Type = 4
                column_pairs.append((entry_series.field, col_index, legend_text, color))
                col_index += 2

            graphs: list[Any] = []
            graph = op.new_graph(template="doubley")
            graphs.append(graph)
            try:
                graph_title = f"{entry.sample} - VSM Temperature Scan"
                graph.set_str("title", graph_title)
                graph.name = f"{entry.sample} - TScan"
                graph.lname = f"{entry.sample} - TScan"
            except Exception:
                graph_title = f"{entry.sample} - VSM Temperature Scan"
                pass
            unique_fields: list[float] = []
            for field_value, _, _, _ in column_pairs:
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
            legend_entries: dict[Any, list[tuple[int, str]]] = {}
            for field_value, base_col, legend_text, color in column_pairs:
                layer = layer_map.get(field_value, graph[0])
                plot_obj = cast(Any, layer.add_plot(data_sheet, coly=base_col + 1, colx=base_col))
                if plot_obj is not None:
                    try:
                        plot_obj.legend = legend_text
                    except Exception:
                        pass
                    try:
                        plot_obj.color = color
                        plot_obj.symbol.color = color
                        plot_obj.symbol.size = 1
                        plot_obj.line.width = 1.2
                    except Exception:
                        pass
                    dataset_index = getattr(plot_obj, "index", None)
                    if isinstance(dataset_index, int):
                        legend_entries.setdefault(layer, []).append((dataset_index, legend_text))
            for layer, entries in legend_entries.items():
                self._set_origin_legend(layer, entries)
            for layer in layer_map.values():
                _style_origin_layer(layer, graph_title)

            if self.show_smoothed_plot:
                smooth_sheet = book.add_sheet()
                smooth_sheet.name = "Smoothed"
                scol = 0
                smooth_pairs: list[tuple[float, int, str, str]] = []
                for entry_series, section_label, legend_text, color in series_info:
                    base_frame, _ = self._dedupe_temperatures(entry_series.frame)
                    working = base_frame.sort_values("temperature")
                    frame = self._smooth_frame(working)
                    temps = frame["temperature"].tolist()
                    signals = frame["signal"].tolist()
                    smooth_sheet.from_list(
                        scol,
                        temps,
                        lname="Temperature",
                        units="°C",
                        comments=section_label,
                        axis="X",
                    )
                    col_x = cast(Any, smooth_sheet.obj.Columns(scol))
                    col_x.Type = 3
                    smooth_sheet.from_list(
                        scol + 1,
                        signals,
                        lname="Signal X (smoothed)",
                        units="emu",
                        comments=legend_text,
                        axis="Y",
                    )
                    col_y = cast(Any, smooth_sheet.obj.Columns(scol + 1))
                    col_y.Type = 4
                    smooth_pairs.append((entry_series.field, scol, legend_text, color))
                    scol += 2
                smooth_graph = op.new_graph(template="doubley")
                graphs.append(smooth_graph)
                try:
                    smooth_title = f"{entry.sample} - Smoothed Signal X"
                    smooth_graph.set_str("title", smooth_title)
                    smooth_graph.name = f"{entry.sample} - Smoothed"
                    smooth_graph.lname = f"{entry.sample} - Smoothed"
                except Exception:
                    smooth_title = f"{entry.sample} - Smoothed Signal X"
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
                legend_entries = {}
                for field_value, base_col, legend_text, color in smooth_pairs:
                    layer = s_layer_map.get(field_value, smooth_graph[0])
                    plot_obj = cast(Any, layer.add_plot(smooth_sheet, coly=base_col + 1, colx=base_col))
                    if plot_obj is not None:
                        try:
                            plot_obj.legend = legend_text
                        except Exception:
                            pass
                        try:
                            plot_obj.color = color
                            plot_obj.symbol.color = color
                            plot_obj.symbol.size = 1
                            plot_obj.line.width = 1.2
                        except Exception:
                            pass
                        dataset_index = getattr(plot_obj, "index", None)
                        if isinstance(dataset_index, int):
                            legend_entries.setdefault(layer, []).append((dataset_index, legend_text))
                for layer, entries in legend_entries.items():
                    self._set_origin_legend(layer, entries)
                for layer in s_layer_map.values():
                    _style_origin_layer(layer, smooth_title)

            if self.show_derivative and include_raw_derivative:
                deriv_sheet = book.add_sheet()
                deriv_sheet.name = "Derivative"
                col = 0
                derivative_column_pairs: list[tuple[float, int, str, str]] = []
                for entry_series, section_label, legend_text, color in series_info:
                    base_frame, _ = self._dedupe_temperatures(entry_series.frame)
                    frame = self._smooth_frame(base_frame.sort_values("temperature"))
                    temps = frame["temperature"].tolist()
                    derivs = self._compute_derivative(frame, smooth=False)
                    deriv_sheet.from_list(
                        col,
                        temps,
                        lname="Temperature",
                        units="°C",
                        comments=section_label,
                        axis="X",
                    )
                    col_x = cast(Any, deriv_sheet.obj.Columns(col))
                    col_x.Type = 3
                    deriv_sheet.from_list(
                        col + 1,
                        derivs,
                        lname="dSignal/dT",
                        units="emu/°C",
                        comments=legend_text,
                        axis="Y",
                    )
                    col_y = cast(Any, deriv_sheet.obj.Columns(col + 1))
                    col_y.Type = 4
                    col += 2
                    derivative_column_pairs.append((entry_series.field, col - 2, legend_text, color))
                deriv_graph = op.new_graph()
                graphs.append(deriv_graph)
                try:
                    deriv_title = f"{entry.sample} - d(Signal X)/dT"
                    deriv_graph.set_str("title", deriv_title)
                    deriv_graph.name = f"{entry.sample} - dSignal/dT"
                    deriv_graph.lname = f"{entry.sample} - dSignal/dT"
                except Exception:
                    deriv_title = f"{entry.sample} - d(Signal X)/dT"
                    pass
                layer = deriv_graph[0]
                try:
                    layer.axis(0).title = "Temperature (°C)"
                    layer.axis(1).title = "d(Signal X)/dT (emu/°C)"
                except Exception:
                    pass
                legend_entries = {}
                for field_value, base_col, legend_text, color in derivative_column_pairs:
                    plot_obj = cast(Any, layer.add_plot(deriv_sheet, coly=base_col + 1, colx=base_col))
                    if plot_obj is not None:
                        try:
                            plot_obj.legend = legend_text
                        except Exception:
                            pass
                        try:
                            plot_obj.color = color
                            plot_obj.symbol.color = color
                            plot_obj.symbol.size = 1
                            plot_obj.line.width = 1.2
                        except Exception:
                            pass
                        dataset_index = getattr(plot_obj, "index", None)
                        if isinstance(dataset_index, int):
                            legend_entries.setdefault(layer, []).append((dataset_index, legend_text))
                try:
                    self._set_origin_legend(layer, legend_entries.get(layer, []))
                except Exception:
                    pass
                _style_origin_layer(layer, deriv_title)

                if self.show_smoothed_derivative and self.smooth_derivative:
                    deriv_sm_sheet = book.add_sheet()
                    deriv_sm_sheet.name = "Derivative (smoothed)"
                    col = 0
                    derivative_sm_pairs: list[tuple[float, int, str, str]] = []
                    for entry_series, section_label, legend_text, color in series_info:
                        base_frame, _ = self._dedupe_temperatures(entry_series.frame)
                        frame = self._smooth_frame(base_frame.sort_values("temperature"))
                        temps = frame["temperature"].tolist()
                        derivs = self._compute_derivative(frame, smooth=True)
                        deriv_sm_sheet.from_list(
                            col,
                            temps,
                            lname="Temperature",
                            units="°C",
                            comments=section_label,
                            axis="X",
                        )
                        col_x = cast(Any, deriv_sm_sheet.obj.Columns(col))
                        col_x.Type = 3
                        deriv_sm_sheet.from_list(
                            col + 1,
                            derivs,
                            lname="dSignal/dT (smoothed)",
                            units="emu/°C",
                            comments=legend_text,
                            axis="Y",
                        )
                        col_y = cast(Any, deriv_sm_sheet.obj.Columns(col + 1))
                        col_y.Type = 4
                        col += 2
                        derivative_sm_pairs.append((entry_series.field, col - 2, legend_text, color))
                    deriv_sm_graph = op.new_graph()
                    graphs.append(deriv_sm_graph)
                    try:
                        deriv_sm_title = f"{entry.sample} - Smoothed d(Signal X)/dT"
                        deriv_sm_graph.set_str("title", deriv_sm_title)
                        deriv_sm_graph.name = f"{entry.sample} - dSignal/dT (smoothed)"
                        deriv_sm_graph.lname = f"{entry.sample} - dSignal/dT (smoothed)"
                    except Exception:
                        deriv_sm_title = f"{entry.sample} - Smoothed d(Signal X)/dT"
                        pass
                    layer = deriv_sm_graph[0]
                    try:
                        layer.axis(0).title = "Temperature (°C)"
                        layer.axis(1).title = "d(Signal X)/dT (emu/°C)"
                    except Exception:
                        pass
                    legend_entries = {}
                    for field_value, base_col, legend_text, color in derivative_sm_pairs:
                        plot_obj = cast(Any, layer.add_plot(deriv_sm_sheet, coly=base_col + 1, colx=base_col))
                        if plot_obj is not None:
                            try:
                                plot_obj.legend = legend_text
                            except Exception:
                                pass
                            try:
                                plot_obj.color = color
                                plot_obj.symbol.color = color
                                plot_obj.symbol.size = 1
                                plot_obj.line.width = 1.2
                            except Exception:
                                pass
                            dataset_index = getattr(plot_obj, "index", None)
                            if isinstance(dataset_index, int):
                                legend_entries.setdefault(layer, []).append((dataset_index, legend_text))
                    try:
                        self._set_origin_legend(layer, legend_entries.get(layer, []))
                    except Exception:
                        pass
                    _style_origin_layer(layer, deriv_sm_title)

            try:
                book.activate()
                op.lt_exec("doc -tf;")
            except Exception:
                pass
            for g in graphs:
                try:
                    g.activate()
                    op.lt_exec("doc -tf;")
                except Exception:
                    pass
            plotted += 1
            self.log(f"Sent {entry.sample} to Origin.")

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
            if self.show_derivative or self.show_smoothed_derivative:
                if self.show_derivative:
                    dname = output_dir / f"{entry.path.stem}_TScan_derivative.txt"
                    d_headers: List[List[str]] = []
                    d_columns: List[List[str]] = []
                    for entry_series in series:
                        base_frame, _ = self._dedupe_temperatures(entry_series.frame)
                        frame = self._smooth_frame(base_frame.sort_values("temperature"))
                        temps = [f"{val:.6f}" for val in frame["temperature"].tolist()]
                        derivs = [f"{val:.6e}" for val in self._compute_derivative(frame, smooth=False)]
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
                if self.show_smoothed_derivative and self.smooth_derivative:
                    sdname = output_dir / f"{entry.path.stem}_TScan_derivative_smoothed.txt"
                    sd_headers: List[List[str]] = []
                    sd_columns: List[List[str]] = []
                    for entry_series in series:
                        base_frame, _ = self._dedupe_temperatures(entry_series.frame)
                        frame = self._smooth_frame(base_frame.sort_values("temperature"))
                        temps = [f"{val:.6f}" for val in frame["temperature"].tolist()]
                        derivs = [f"{val:.6e}" for val in self._compute_derivative(frame, smooth=True)]
                        sd_columns.append(temps)
                        sd_columns.append(derivs)
                        sd_headers.append(["Temperature", "dSignal/dT (smoothed)"])
                        sd_headers.append(["°C", "emu/°C"])
                        legend = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction, entry_series.segment_index)} (Section {entry_series.segment_index + 1})"
                        sd_headers.append([f"Section {entry_series.segment_index + 1}", legend])
                    with sdname.open("w", newline="", encoding="utf-8") as handle:
                        writer = csv.writer(handle, delimiter="\t")
                        if sd_headers:
                            for header_row in zip_longest(*sd_headers, fillvalue=""):
                                writer.writerow(header_row)
                        for data_row in zip_longest(*sd_columns, fillvalue=""):
                            writer.writerow(data_row)
                    self.log(f"Exported {sdname.name}")

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

    smooth_deriv_var = tk.BooleanVar(app, value=processor.smooth_derivative)
    ttk.Checkbutton(
        app.options_frame,
        text="Smooth derivatives",
        variable=smooth_deriv_var,
        command=lambda: processor.set_smooth_derivative(smooth_deriv_var.get()),
    ).pack(side=tk.LEFT, padx=(12, 0))

    sm_deriv_var = tk.BooleanVar(app, value=processor.show_smoothed_derivative)
    ttk.Checkbutton(
        app.options_frame,
        text="Plot smoothed derivatives",
        variable=sm_deriv_var,
        command=lambda: processor.set_show_smoothed_derivative(sm_deriv_var.get()),
    ).pack(side=tk.LEFT, padx=(12, 0))

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

    dmed_label = ttk.Label(app.options_frame, text="d/dT median:")
    dmed_label.pack(side=tk.LEFT, padx=(12, 4))
    dmed_entry = ttk.Entry(app.options_frame, width=4)
    dmed_entry.insert(0, str(processor.derivative_median_window))
    dmed_entry.pack(side=tk.LEFT)
    dma_label = ttk.Label(app.options_frame, text="d/dT MA:")
    dma_label.pack(side=tk.LEFT, padx=(4, 4))
    dma_entry = ttk.Entry(app.options_frame, width=4)
    dma_entry.insert(0, str(processor.derivative_moving_avg_window))
    dma_entry.pack(side=tk.LEFT)

    def _apply_smoothing() -> None:
        try:
            median = int(med_entry.get())
            ma = int(ma_entry.get())
        except Exception:
            median = processor.median_window
            ma = processor.moving_avg_window
        try:
            d_median = int(dmed_entry.get())
            d_ma = int(dma_entry.get())
        except Exception:
            d_median = processor.derivative_median_window
            d_ma = processor.derivative_moving_avg_window
        processor.set_smoothing_windows(median, ma)
        processor.set_derivative_smoothing_windows(d_median, d_ma)

    ttk.Button(
        app.options_frame,
        text="Apply smoothing",
        command=_apply_smoothing,
    ).pack(side=tk.LEFT, padx=(6, 0))
    app.mainloop()


if __name__ == "__main__":
    main()
