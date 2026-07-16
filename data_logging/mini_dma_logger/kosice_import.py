"""Read-only indexing and parsing for Košice current-annealing folders.

The Košice database is intentionally treated as a folder source rather than a
Microwire Data Builder project.  This module contains no Qt code so filename
matching and curve parsing can be reused by UI workers and headless tools.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd


SUPPORTED_EXTENSIONS = frozenset({".dat", ".txt"})
ANNEALING_DIRECTORY_NAMES = frozenset({"currentannealing", "currentannealingdata"})
_COMPOSITION_RE = re.compile(r"^(?P<composition>(?:[A-Z][a-z]?\d+(?:[.,]\d+)?)+)")
_WIRE_RE = re.compile(r"^\s*[-_]?\s*(?P<draw>\d+)\s*[_-]\s*(?P<piece>\d+)(?=$|[\s_-])")
_SETPOINT_RE = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*mA\b", re.IGNORECASE)


def _normalized_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _microwire_key(value: object) -> tuple[int, int] | None:
    numbers = re.findall(r"\d+", str(value or ""))
    if len(numbers) < 2:
        return None
    return int(numbers[0]), int(numbers[1])


@dataclass(frozen=True)
class AnnealingFolderRecord:
    path: Path
    composition: str
    draw: int
    piece: int
    annotation: str
    setpoint_mA: float | None

    @property
    def microwire(self) -> str:
        return f"{self.draw}/{self.piece}"

    @property
    def curve_label(self) -> str:
        return self.annotation or self.path.stem


@dataclass(frozen=True)
class AnnealingFolderIndex:
    root: Path
    source_label: str
    records: tuple[AnnealingFolderRecord, ...]
    data_directories: tuple[Path, ...]
    unsupported_files: tuple[Path, ...] = ()
    skipped_files: tuple[Path, ...] = ()

    def matching(self, composition: object, microwire: object) -> tuple[AnnealingFolderRecord, ...]:
        composition_key = _normalized_token(composition)
        wire_key = _microwire_key(microwire)
        if not composition_key or wire_key is None:
            return ()
        return tuple(
            record
            for record in self.records
            if _normalized_token(record.composition) == composition_key
            and (record.draw, record.piece) == wire_key
        )

    def suggestions(self) -> dict[str, tuple[str, ...]]:
        values: dict[str, set[tuple[int, int]]] = {}
        for record in self.records:
            values.setdefault(record.composition, set()).add((record.draw, record.piece))
        return {
            composition: tuple(f"{draw}/{piece}" for draw, piece in sorted(wires))
            for composition, wires in sorted(values.items(), key=lambda item: item[0].lower())
        }


def parse_annealing_filename(path: Path) -> AnnealingFolderRecord | None:
    """Parse only a leading composition and immediately following wire pair.

    Everything after the wire pair is retained as a curve annotation.  This
    deliberately avoids interpreting run/cycle tokens as specimen identity.
    """

    composition_match = _COMPOSITION_RE.match(path.stem)
    if composition_match is None:
        return None
    composition = composition_match.group("composition").replace(",", ".")
    remainder = path.stem[composition_match.end() :]
    wire_match = _WIRE_RE.match(remainder)
    if wire_match is None:
        return None
    draw = int(wire_match.group("draw"))
    piece = int(wire_match.group("piece"))
    annotation = remainder[wire_match.end() :].strip(" _-")
    setpoint_match = _SETPOINT_RE.search(annotation)
    setpoint_mA = None
    if setpoint_match is not None:
        setpoint_mA = float(setpoint_match.group(1).replace(",", "."))
    return AnnealingFolderRecord(
        path=path,
        composition=composition,
        draw=draw,
        piece=piece,
        annotation=annotation,
        setpoint_mA=setpoint_mA,
    )


def _directory_key(path: Path) -> str:
    return re.sub(r"[^a-z]+", "", path.name.lower())


def _candidate_data_directories(root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []

    def _add(candidate: Path) -> None:
        if candidate not in candidates and candidate.is_dir():
            candidates.append(candidate)

    if _directory_key(root) in ANNEALING_DIRECTORY_NAMES:
        _add(root)
    for parent in (root, root.parent):
        try:
            children = tuple(parent.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and _directory_key(child) in ANNEALING_DIRECTORY_NAMES:
                _add(child)
    if not candidates:
        try:
            if any(child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS for child in root.iterdir()):
                _add(root)
        except OSError:
            pass
    return tuple(candidates)


def build_annealing_folder_index(
    root: Path,
    *,
    source_label: str,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str], None] | None = None,
) -> AnnealingFolderIndex:
    """Index supported flat annealing directories without mutating the source."""

    root = Path(root)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Folder was not found: {root}")
    is_cancelled = cancelled or (lambda: False)
    report = progress or (lambda _message: None)
    data_directories = _candidate_data_directories(root)
    if not data_directories:
        raise ValueError("No Current Annealing folder or supported flat annealing files were found.")

    unsupported: list[Path] = []
    try:
        unsupported.extend(
            child for child in root.iterdir() if child.is_file() and child.suffix.lower() == ".opju"
        )
    except OSError:
        pass

    records: list[AnnealingFolderRecord] = []
    skipped: list[Path] = []
    for data_directory in data_directories:
        report(f"Scanning {source_label}: {data_directory.name}...")
        for path in sorted(data_directory.iterdir(), key=lambda item: item.name.lower()):
            if is_cancelled():
                raise InterruptedError("Annealing folder scan cancelled.")
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            record = parse_annealing_filename(path)
            if record is None:
                skipped.append(path)
                continue
            records.append(record)
    records.sort(
        key=lambda item: (
            item.composition.lower(),
            item.draw,
            item.piece,
            item.setpoint_mA if item.setpoint_mA is not None else math.inf,
            item.path.name.lower(),
        )
    )
    return AnnealingFolderIndex(
        root=root,
        source_label=source_label,
        records=tuple(records),
        data_directories=data_directories,
        unsupported_files=tuple(sorted(set(unsupported), key=lambda item: item.name.lower())),
        skipped_files=tuple(skipped),
    )


def _numeric_tokens(line: str) -> list[float] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    tokens = re.split(r"\s+", stripped)
    values: list[float] = []
    for token in tokens:
        try:
            values.append(float(token.replace("\u2212", "-").replace(",", ".")))
        except ValueError:
            return None
    return values or None


def _iter_numeric_rows(lines: Iterable[str]) -> list[list[float]]:
    return [values for line in lines if (values := _numeric_tokens(line)) is not None]


def _legacy_four_column_current_scale_to_mA(
    samples: Iterable[tuple[float, float, float]],
) -> float:
    """Resolve misleading legacy current headers from electrical consistency.

    Some Košice ``Iset/Ireal/Ureal/R`` files label the current columns as mA
    even though their numeric values are amperes.  Comparing ``U / I`` with the
    stored resistance distinguishes those files from genuinely-mA tables.
    Preserve the declared mA interpretation when the evidence is insufficient.
    """

    errors_if_a: list[float] = []
    errors_if_ma: list[float] = []
    for raw_current, voltage_v, resistance_ohm in samples:
        if raw_current == 0.0 or resistance_ohm <= 0.0:
            continue
        denominator = max(abs(resistance_ohm), 1e-12)
        errors_if_a.append(abs((voltage_v / raw_current) - resistance_ohm) / denominator)
        errors_if_ma.append(
            abs((voltage_v / (raw_current / 1000.0)) - resistance_ohm) / denominator
        )
    if not errors_if_a or not errors_if_ma:
        return 1.0
    errors_if_a.sort()
    errors_if_ma.sort()
    median_index = len(errors_if_a) // 2
    amp_error = errors_if_a[median_index]
    milliamp_error = errors_if_ma[median_index]
    if amp_error <= 0.25 and amp_error * 10.0 < milliamp_error:
        return 1000.0
    return 1.0


def load_annealing_curve(path: Path) -> pd.DataFrame:
    """Load Košice tabular schemas into the preview's canonical columns.

    Supported forms are the three-column ``.txt`` schema, the older four-column
    ``Iset/Ireal/Voltage/Resistance`` ``.dat`` schema, and the current six-column
    ``Cycle/Iset/Ireal/Voltage/Resistance/Power`` ``.dat`` schema.
    """

    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    rows = _iter_numeric_rows(lines)
    if not rows:
        raise ValueError(f"{path.name}: no numeric annealing rows found")
    width = max(len(row) for row in rows)
    header_text = " ".join(line.lower() for line in lines[:4])
    has_cycle_column = "cycle" in header_text

    legacy_four_column = False
    if width >= 5 and (has_cycle_column or path.suffix.lower() == ".dat"):
        current_index, voltage_index, resistance_index = 2, 3, 4
    elif width >= 4 and path.suffix.lower() == ".dat":
        current_index, voltage_index, resistance_index = 1, 2, 3
        legacy_four_column = True
    elif width >= 3:
        current_index, voltage_index, resistance_index = 0, 1, 2
    else:
        raise ValueError(f"{path.name}: expected at least three numeric columns")

    parsed: list[tuple[float, float, float]] = []
    required_width = max(current_index, voltage_index, resistance_index) + 1
    for row in rows:
        if len(row) < required_width:
            continue
        current_mA = row[current_index]
        voltage_v = row[voltage_index]
        resistance_ohm = row[resistance_index]
        if not all(math.isfinite(value) for value in (current_mA, voltage_v, resistance_ohm)):
            continue
        if current_mA == 0.0 or resistance_ohm <= 0.0:
            continue
        parsed.append((current_mA, voltage_v, resistance_ohm))
    if not parsed:
        raise ValueError(f"{path.name}: no usable current/resistance samples found")
    current_scale_to_mA = (
        _legacy_four_column_current_scale_to_mA(parsed)
        if legacy_four_column
        else 1.0
    )
    frame = pd.DataFrame(parsed, columns=["I_mA", "V_V", "R_Ohm"])
    frame["I_mA"] *= current_scale_to_mA
    frame["I_A"] = frame["I_mA"] / 1000.0
    return frame[["I_A", "I_mA", "V_V", "R_Ohm"]]


__all__ = [
    "AnnealingFolderIndex",
    "AnnealingFolderRecord",
    "build_annealing_folder_index",
    "load_annealing_curve",
    "parse_annealing_filename",
]
