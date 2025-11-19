#!/usr/bin/env python3
"""Tkinter UI for plotting VSM temperature scan data (Signal X vs Temperature)."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pandas as pd
import tkinter as tk  # noqa: F401 - required for Tk dialogs used in _shared
from tkinter import ttk
import numpy as np

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
    frame: pd.DataFrame


class VSMTemperatureScanProcessor(SimpleScriptProcessor):
    """Processor powering the Tk UI."""

    def __init__(self) -> None:
        super().__init__()
        self.split_directions: bool = True
        self.show_derivative: bool = False

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
        header_finished = False
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not header_finished:
                    if line.startswith("@Samplename:"):
                        sample_name = line.split(":", 1)[1].strip() or sample_name
                    if line.startswith("@@End of Header."):
                        header_finished = True
                    continue
                if not line or line.startswith("@@Data") or line.startswith("New Section"):
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
                    data_rows.append(
                        {"temperature": temperature, "field": field, "signal": signal}
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

    def _build_series(self, frame: pd.DataFrame) -> list[PlotSeries]:
        series: list[PlotSeries] = []
        if frame.empty:
            return series
        for field_value, subset in frame.groupby("field"):
            field_float = self._to_float(field_value)
            if field_float is None:
                continue
            subset = subset.sort_values("temperature")
            if self.split_directions:
                for direction, segment in self._split_segments(subset):
                    series.append(PlotSeries(field_float, direction, segment))
            else:
                series.append(PlotSeries(field_float, "all", subset))
        series.sort(key=lambda item: item.field, reverse=True)
        return series

    def _split_segments(self, frame: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
        if len(frame) < 2:
            return [("all", frame)]
        temps = frame["temperature"].to_numpy()
        segments: list[tuple[str, pd.DataFrame]] = []
        start = 0
        last_dir: str | None = None
        for idx in range(1, len(frame)):
            delta = temps[idx] - temps[idx - 1]
            curr_dir: str
            if abs(delta) < 1e-6:
                curr_dir = last_dir or "flat"
            else:
                curr_dir = "up" if delta > 0 else "down"
            if last_dir is None:
                last_dir = curr_dir
                continue
            if curr_dir != last_dir:
                segments.append((last_dir, frame.iloc[start:idx].copy()))
                start = idx - 1
                last_dir = curr_dir
        segments.append((last_dir or "flat", frame.iloc[start:].copy()))
        return segments

    def _direction_suffix(self, direction: str) -> str:
        if direction in {"all", "flat"}:
            return ""
        return f"_{direction}"

    def _direction_label(self, direction: str) -> str:
        if direction == "up":
            return " ↑"
        if direction == "down":
            return " ↓"
        if direction == "flat":
            return " (flat)"
        return ""

    def _compute_derivative(self, frame: pd.DataFrame) -> list[float]:
        if len(frame) < 2:
            return [0.0 for _ in frame["temperature"]]
        temp_series = frame["temperature"].astype(float)
        signal_series = frame["signal"].astype(float)
        diffs = signal_series.diff() / temp_series.diff().replace(0, pd.NA)
        diffs = diffs.replace([pd.NA, np.inf, -np.inf], 0.0).fillna(method="bfill").fillna(0.0)
        return diffs.to_numpy().tolist()

    def set_split_directions(self, enabled: bool) -> None:
        self.split_directions = bool(enabled)

    def set_show_derivative(self, enabled: bool) -> None:
        self.show_derivative = bool(enabled)

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
            colors = ["#2563eb", "#dc2626", "#059669", "#d97706"]
            handles = []
            field_axes: Dict[float, Any] = {}
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
                    return ax_right
                field_axes[field] = ax_left
                return ax_left

            for idx, entry_series in enumerate(series):
                axis = pick_axis(entry_series.field)
                color = colors[idx % len(colors)]
                label = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction)}"
                line = axis.plot(
                    entry_series.frame["temperature"],
                    entry_series.frame["signal"],
                    color=color,
                    label=label,
                    linewidth=1.6,
                )[0]
                handles.append(line)
                if self.show_derivative:
                    derivative = self._compute_derivative(entry_series.frame)
                    if ax_deriv is not None:
                        dlabel = f"d/dT {label}"
                        dline = ax_deriv.plot(
                            entry_series.frame["temperature"],
                            derivative,
                            color=color,
                            linestyle="--",
                            linewidth=1.2,
                            label=dlabel,
                        )[0]
                        deriv_handles.append(dline)

            ax_left.legend(handles=handles, loc="best")
            fig.tight_layout()
            plt.show()
            if fig_deriv is not None and ax_deriv is not None and deriv_handles:
                ax_deriv.legend(handles=deriv_handles, loc="best")
                fig_deriv.tight_layout()
                plt.show()
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
        for entry in dataset:
            series = self._build_series(entry.dataframe.copy())
            if not series:
                continue
            book = op.new_book("w")
            book.lname = f"{entry.sample} (TScan)"
            wks = book[0]
            wks.name = "Data"
            col_index = 0
            column_pairs: list[tuple[float, str, int]] = []
            for entry_series in series:
                frame = entry_series.frame.sort_values("temperature")
                temps = frame["temperature"].tolist()
                signals = frame["signal"].tolist()
                wks.from_list(col_index, temps)
                col_x = wks.obj.Columns(col_index)
                col_x.LongName = f"Temperature {self._direction_label(entry_series.direction)}"
                col_x.Units = "°C"
                col_x.Comment = f"{entry_series.field:.0f} Oe{self._direction_label(entry_series.direction)}"
                col_x.Type = 3
                wks.from_list(col_index + 1, signals)
                col_y = wks.obj.Columns(col_index + 1)
                suffix = self._direction_label(entry_series.direction)
                col_y.LongName = f"Signal X {entry_series.field:.0f} Oe{suffix}"
                col_y.Units = "emu"
                col_y.Comment = f"Applied field {entry_series.field:.0f} Oe{suffix}"
                col_y.Type = 4
                column_pairs.append((entry_series.field, entry_series.direction, col_index))
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
            for field_value, direction, base_col in column_pairs:
                layer = layer_map.get(field_value, graph[0])
                plot_obj = layer.add_plot(wks, coly=base_col + 1, colx=base_col)
                if plot_obj is not None:
                    try:
                        plot_obj.legend = f"{field_value:.0f} Oe{self._direction_label(direction)}"
                    except Exception:
                        pass
            try:
                graph.activate()
                op.lt_exec("doc -tf;")
            except Exception:
                pass
            plotted += 1
            self.log(f"Sent {entry.sample} to Origin.")
        if plotted == 0:
            raise RuntimeError("No data was available for Origin plotting.")

    # ------------------------------------------------------------------ TXT export
    def export_txt(self, dataset: list[VSMEntry], output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for entry in dataset:
            series = self._build_series(entry.dataframe.copy())
            for entry_series in series:
                suffix = self._direction_suffix(entry_series.direction)
                fname = (
                    output_dir
                    / f"{entry.path.stem}_{int(entry_series.field)}Oe{suffix}.txt"
                )
                frame = entry_series.frame.sort_values("temperature")
                with fname.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle, delimiter="\t")
                    writer.writerow(
                        [
                            "# Column 1: Temperature",
                            "Units: degC",
                            f"Comment: Sample temperature {self._direction_label(entry_series.direction)}",
                        ]
                    )
                    writer.writerow(
                        [
                            "# Column 2: Signal X direction",
                            "Units: emu",
                            f"Comment: Applied field {entry_series.field:.0f} Oe{self._direction_label(entry_series.direction)}",
                        ]
                    )
                    writer.writerow(["Temperature (°C)", "Signal X (emu)"])
                    for _, row in frame.iterrows():
                        writer.writerow(
                            [
                                f"{row['temperature']:.6f}",
                                f"{row['signal']:.6e}",
                            ]
                        )
                self.log(f"Exported {fname.name}")
        self.log(f"TXT export complete: {output_dir}")


def main() -> None:
    processor = VSMTemperatureScanProcessor()
    app = SimpleScriptApp("VSM Temperature Scan", processor)
    split_var = tk.BooleanVar(value=True)
    processor.set_split_directions(True)
    ttk.Checkbutton(
        app.options_frame,
        text="Separate heating/cooling",
        variable=split_var,
        command=lambda: processor.set_split_directions(split_var.get()),
    ).pack(side=tk.LEFT, padx=(0, 12))

    deriv_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        app.options_frame,
        text="Plot derivatives",
        variable=deriv_var,
        command=lambda: processor.set_show_derivative(deriv_var.get()),
    ).pack(side=tk.LEFT)
    app.mainloop()


if __name__ == "__main__":
    main()
