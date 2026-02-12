"""Core parsing and entropy helpers for VSM isotherm (VIR) files."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
import pandas as pd

ANGLE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])a(-?\d+(?:\.\d+)?)", re.IGNORECASE)
TEMP_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])T(-?\d+(?:\.\d+)?)", re.IGNORECASE)
FIELD_ANGLE_RE = re.compile(
    r"Set Field Angle to\s+([-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
SAMPLE_ANGLE_OFFSET_RE = re.compile(
    r"Sample Angle Offset\s*=\s*([-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
SET_TEMPERATURE_RE = re.compile(
    r"Set Sample Temperature to\s+([-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

FIELD_KEYS: tuple[str, ...] = (
    "Applied_Field_For_Plot_",
    "Applied_Field",
    "Raw_Applied_Field_For_Plot_",
    "Raw_Applied_Field",
)
SIGNAL_KEYS: tuple[str, ...] = (
    "Signal_X_direction",
    "Signal_parallel_with_sample",
    "Raw_Signal_Mx",
)
TEMPERATURE_KEYS: tuple[str, ...] = (
    "Temperature",
    "Raw_Temperature",
    "Temperature_2",
)
VIR_FILE_SUFFIX = ".vsm-vir-data"


@dataclass
class VSMIsothermEntry:
    """Single VIR file parsed into one isotherm curve."""

    path: Path
    sample: str
    angle: float
    temperature: float
    dataframe: pd.DataFrame  # columns: field, signal


@dataclass
class EntropyResult:
    """Calculated entropy summary from isothermal M(H) curves."""

    frame: pd.DataFrame  # columns: temperature, dS_<field>Oe...
    field_levels: list[float]
    max_delta_field: float


class VSMIsothermProcessor:
    """Parse VIR data files and calculate a Maxwell-relation entropy estimate."""

    def __init__(self) -> None:
        self._logger: Callable[[str], None] = lambda message: None

    def attach_logger(self, callback: Callable[[str], None]) -> None:
        self._logger = callback

    def log(self, message: str) -> None:
        try:
            self._logger(message)
        except Exception:
            pass

    def load(self, paths: Iterable[Path]) -> list[VSMIsothermEntry]:
        entries: list[VSMIsothermEntry] = []
        for path in paths:
            if not path.is_file():
                continue
            if path.suffix.lower() != VIR_FILE_SUFFIX:
                self.log(f"{path.name}: not a VIR file; skipping.")
                continue
            frame, sample, angle, temperature = self._parse_file(path)
            if frame.empty:
                self.log(f"{path.name}: no usable VIR rows were found.")
                continue
            if angle is None:
                self.log(f"{path.name}: angle metadata missing; skipping file.")
                continue
            if temperature is None:
                self.log(f"{path.name}: temperature metadata missing; skipping file.")
                continue
            entries.append(
                VSMIsothermEntry(
                    path=path,
                    sample=sample,
                    angle=self._normalize_angle(angle),
                    temperature=float(round(float(temperature), 3)),
                    dataframe=frame,
                )
            )
        if not entries:
            raise RuntimeError("No usable VSM isotherm data was found.")
        self.log(f"Loaded {len(entries)} VSM isotherm dataset(s).")
        return entries

    def group_by_sample_angle(
        self,
        entries: Sequence[VSMIsothermEntry],
    ) -> dict[tuple[str, float], list[VSMIsothermEntry]]:
        grouped: dict[tuple[str, float], list[VSMIsothermEntry]] = defaultdict(list)
        for entry in entries:
            grouped[(entry.sample, entry.angle)].append(entry)
        collapsed: dict[tuple[str, float], list[VSMIsothermEntry]] = {}
        for key, values in grouped.items():
            values.sort(key=lambda item: (item.temperature, item.path.name.lower()))
            collapsed[key] = self._collapse_temperature_duplicates(values)
        return dict(sorted(collapsed.items(), key=lambda item: (item[0][0].lower(), item[0][1])))

    def compute_entropy(
        self,
        entries: Sequence[VSMIsothermEntry],
        *,
        temperature_bin_c: float = 1.0,
        min_curves: int = 3,
        min_points_per_curve: int = 12,
        field_levels_oe: Sequence[float] | None = None,
    ) -> EntropyResult | None:
        prepared: list[tuple[float, np.ndarray, np.ndarray]] = []
        for entry in entries:
            frame = entry.dataframe.copy()
            frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["field", "signal"])
            if frame.empty:
                continue
            frame = frame.sort_values("field").drop_duplicates(subset="field", keep="last")
            if len(frame.index) < min_points_per_curve:
                continue
            fields = frame["field"].to_numpy(dtype=float)
            signals = frame["signal"].to_numpy(dtype=float)
            if np.nanmax(fields) <= np.nanmin(fields):
                continue
            prepared.append((entry.temperature, fields, signals))
        if len(prepared) < min_curves:
            return None

        best_max_field = max(float(np.nanmax(fields)) for _, fields, _ in prepared)
        if best_max_field > 0:
            keep_threshold = best_max_field * 0.8
            filtered = [row for row in prepared if float(np.nanmax(row[1])) >= keep_threshold]
            if len(filtered) >= min_curves:
                prepared = filtered

        h_min = max(float(np.nanmin(fields)) for _, fields, _ in prepared)
        h_max = min(float(np.nanmax(fields)) for _, fields, _ in prepared)
        if not np.isfinite(h_min) or not np.isfinite(h_max) or h_max <= h_min:
            return None

        min_points = min(len(fields) for _, fields, _ in prepared)
        grid_points = int(np.clip(min_points, 80, 400))
        field_grid = np.linspace(h_min, h_max, grid_points)
        delta_h_grid = field_grid - field_grid[0]

        bucketed: dict[float, list[tuple[float, np.ndarray]]] = defaultdict(list)
        for temperature, fields, signals in prepared:
            interp_signal = np.interp(field_grid, fields, signals)
            if temperature_bin_c > 0:
                bucket = round(float(temperature) / temperature_bin_c) * temperature_bin_c
            else:
                bucket = float(temperature)
            bucketed[float(bucket)].append((float(temperature), interp_signal))

        merged_curves: list[tuple[float, np.ndarray]] = []
        for values in bucketed.values():
            temps = [item[0] for item in values]
            stack = np.vstack([item[1] for item in values])
            merged_curves.append((float(np.mean(temps)), stack.mean(axis=0)))
        merged_curves.sort(key=lambda row: row[0])
        if len(merged_curves) < min_curves:
            return None

        mid_temperatures: list[float] = []
        entropy_profiles: list[np.ndarray] = []
        for (t0, m0), (t1, m1) in zip(merged_curves[:-1], merged_curves[1:]):
            dt = float(t1 - t0)
            if abs(dt) < 1e-9:
                continue
            dmdt = (m1 - m0) / dt
            integral = np.zeros_like(field_grid)
            increments = 0.5 * (dmdt[1:] + dmdt[:-1]) * np.diff(field_grid)
            integral[1:] = np.cumsum(increments)
            entropy_profiles.append(-integral)
            mid_temperatures.append((t0 + t1) * 0.5)
        if len(mid_temperatures) < 2:
            return None

        max_delta_field = float(delta_h_grid[-1])
        if field_levels_oe:
            field_levels = self._prepare_entropy_levels(
                field_levels_oe,
                max_delta_field=max_delta_field,
            )
        else:
            field_levels = self._choose_entropy_levels(max_delta_field)
        if not field_levels:
            return None

        data: dict[str, list[float]] = {"temperature": [float(temp) for temp in mid_temperatures]}
        for level in field_levels:
            index = int(np.argmin(np.abs(delta_h_grid - level)))
            key = f"dS_{int(round(level))}Oe"
            data[key] = [float(profile[index]) for profile in entropy_profiles]
        frame = pd.DataFrame(data).sort_values("temperature").reset_index(drop=True)
        return EntropyResult(
            frame=frame,
            field_levels=field_levels,
            max_delta_field=max_delta_field,
        )

    # ------------------------------------------------------------------ parsing helpers
    def _parse_file(
        self,
        path: Path,
    ) -> tuple[pd.DataFrame, str, float | None, float | None]:
        sample_name = path.stem
        angle = self._parse_angle_from_filename(path)
        temperature = self._parse_temperature_from_filename(path)

        column_names: list[str] = []
        data_rows: list[dict[str, float]] = []
        observed_angles: list[float] = []
        observed_temps: list[float] = []
        header_finished = False
        in_data = False

        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue

                if line.startswith("@Samplename:"):
                    sample_name = line.split(":", 1)[1].strip() or sample_name
                if angle is None:
                    angle = self._extract_number(FIELD_ANGLE_RE, line)
                if angle is None:
                    angle = self._extract_number(SAMPLE_ANGLE_OFFSET_RE, line)
                if temperature is None:
                    temperature = self._extract_number(SET_TEMPERATURE_RE, line)

                if line.startswith("@@End of Header."):
                    header_finished = True
                    continue

                if not header_finished:
                    continue

                if not column_names and line.startswith("Time_since_start"):
                    column_names = [token.strip() for token in line.split()]
                    continue

                if line.startswith("@@Data"):
                    in_data = True
                    continue

                if line.startswith("@@END Data") or line.startswith("@@Final Manipulated Data"):
                    if in_data:
                        break
                    continue

                if not in_data:
                    continue
                if line.startswith("New Section") or line.startswith("@"):
                    continue

                values = [token for token in line.split() if token]
                if not values:
                    continue

                if column_names and len(values) >= len(column_names):
                    row = dict(zip(column_names, values))
                    field_value = self._pick_float(row, FIELD_KEYS)
                    signal_value = self._pick_float(row, SIGNAL_KEYS)
                    angle_value = self._pick_float(row, ("Field_Angle",))
                    temp_value = self._pick_float(row, TEMPERATURE_KEYS)
                    if angle_value is not None:
                        observed_angles.append(angle_value)
                    if temp_value is not None:
                        observed_temps.append(temp_value)
                else:
                    field_value, signal_value = self._pick_fallback_pair(values)

                if field_value is None or signal_value is None:
                    continue
                data_rows.append({"field": float(field_value), "signal": float(signal_value)})

        if angle is None and observed_angles:
            angle = float(np.nanmedian(observed_angles))
        if temperature is None and observed_temps:
            temperature = float(np.nanmedian(observed_temps))

        frame = pd.DataFrame.from_records(data_rows, columns=["field", "signal"])
        if not frame.empty:
            frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["field", "signal"])
            frame = frame.sort_values("field").reset_index(drop=True)
        return frame, sample_name, angle, temperature

    def _parse_angle_from_filename(self, path: Path) -> float | None:
        match = ANGLE_TOKEN_RE.search(path.name)
        if not match:
            return None
        return self._to_float(match.group(1))

    def _parse_temperature_from_filename(self, path: Path) -> float | None:
        match = TEMP_TOKEN_RE.search(path.name)
        if not match:
            return None
        return self._to_float(match.group(1))

    def _pick_float(self, mapping: dict[str, str], keys: Sequence[str]) -> float | None:
        for key in keys:
            if key not in mapping:
                continue
            value = self._to_float(mapping[key])
            if value is not None:
                return value
        return None

    def _pick_fallback_pair(self, values: Sequence[str]) -> tuple[float | None, float | None]:
        # Most VIR exports include field at index 5 and signal-x at index 10.
        if len(values) >= 11:
            return self._to_float(values[5]), self._to_float(values[10])
        if len(values) >= 3:
            return self._to_float(values[1]), self._to_float(values[-1])
        return None, None

    def _extract_number(self, pattern: re.Pattern[str], text: str) -> float | None:
        match = pattern.search(text)
        if not match:
            return None
        return self._to_float(match.group(1))

    def _to_float(self, value: object) -> float | None:
        try:
            return float(str(value).strip())
        except Exception:
            return None

    def _normalize_angle(self, angle: float) -> float:
        value = float(angle)
        if abs(value) < 1e-6:
            return 0.0
        return float(round(value, 3))

    def _choose_entropy_levels(self, max_delta_field: float) -> list[float]:
        if not np.isfinite(max_delta_field) or max_delta_field <= 0:
            return []
        if max_delta_field < 1200:
            return [float(max_delta_field)]
        fractions = [0.5, 1.0] if max_delta_field < 4000 else [0.25, 0.5, 0.75, 1.0]
        candidates: list[float] = []
        for fraction in fractions:
            target = max_delta_field * fraction
            rounded = max(100.0, round(target / 100.0) * 100.0)
            rounded = min(rounded, max_delta_field)
            candidates.append(float(rounded))
        if not candidates:
            candidates.append(float(max_delta_field))

        unique: list[float] = []
        for value in sorted(candidates):
            if not unique or not np.isclose(value, unique[-1], atol=1e-6):
                unique.append(value)
        if not np.isclose(unique[-1], max_delta_field, atol=1e-6):
            unique.append(float(max_delta_field))
        return unique

    def _prepare_entropy_levels(
        self,
        requested: Sequence[float],
        *,
        max_delta_field: float,
    ) -> list[float]:
        if not np.isfinite(max_delta_field) or max_delta_field <= 0:
            return []
        filtered: list[float] = []
        for raw in requested:
            try:
                value = float(raw)
            except Exception:
                continue
            if not np.isfinite(value) or value <= 0:
                continue
            if value > max_delta_field:
                continue
            filtered.append(value)
        if not filtered:
            return []

        collapsed: dict[int, float] = {}
        for value in sorted(filtered):
            collapsed[int(round(value))] = float(value)
        return [collapsed[key] for key in sorted(collapsed)]

    def _collapse_temperature_duplicates(
        self,
        entries: Sequence[VSMIsothermEntry],
        *,
        temperature_bin_c: float = 1.0,
    ) -> list[VSMIsothermEntry]:
        if not entries:
            return []
        bucketed: dict[float, list[VSMIsothermEntry]] = defaultdict(list)
        for entry in entries:
            bucket = self._temperature_bucket(entry.temperature, temperature_bin_c=temperature_bin_c)
            bucketed[bucket].append(entry)

        collapsed: list[VSMIsothermEntry] = []
        for bucket in sorted(bucketed):
            candidates = bucketed[bucket]
            if len(candidates) == 1:
                collapsed.append(candidates[0])
                continue

            chosen = max(
                candidates,
                key=lambda item: (
                    self._entry_max_abs_field(item),
                    len(item.dataframe.index),
                    self._entry_field_span(item),
                    -abs(item.temperature - bucket),
                ),
            )
            collapsed.append(chosen)
            field_values = [self._entry_max_abs_field(item) for item in candidates]
            if max(field_values) > min(field_values) * 1.5 or len(candidates) > 2:
                self.log(
                    f"{chosen.sample} @ {self._normalize_angle(chosen.angle):g}° "
                    f"{bucket:.1f} °C: merged {len(candidates)} runs into {chosen.path.name}."
                )
        collapsed.sort(key=lambda item: (item.temperature, item.path.name.lower()))
        return collapsed

    def _temperature_bucket(self, temperature: float, *, temperature_bin_c: float) -> float:
        if temperature_bin_c <= 0:
            return float(temperature)
        return float(round(float(temperature) / temperature_bin_c) * temperature_bin_c)

    def _entry_max_abs_field(self, entry: VSMIsothermEntry) -> float:
        if entry.dataframe.empty or "field" not in entry.dataframe:
            return 0.0
        values = entry.dataframe["field"].to_numpy(dtype=float)
        if values.size == 0:
            return 0.0
        return float(np.nanmax(np.abs(values)))

    def _entry_field_span(self, entry: VSMIsothermEntry) -> float:
        if entry.dataframe.empty or "field" not in entry.dataframe:
            return 0.0
        values = entry.dataframe["field"].to_numpy(dtype=float)
        if values.size == 0:
            return 0.0
        return float(np.nanmax(values) - np.nanmin(values))


__all__ = [
    "EntropyResult",
    "VSMIsothermEntry",
    "VSMIsothermProcessor",
]
