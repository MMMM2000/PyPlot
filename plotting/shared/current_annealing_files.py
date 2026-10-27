"""Discover Current Annealing measurements without importing the plotting stack."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import re


_SUPPORT_FOLDERS = {"metadata", "archive", "archives", "artifacts", "logs", "__pycache__"}
_SUPPORT_FILES = {"run_log.txt", "notes.txt", "readme.txt", "log.txt"}


def resolve_measurement_path(path: str | Path) -> Path:
    """Resolve a file or a run folder, preferring its local metadata data_file."""
    source = Path(path)
    if not source.is_dir():
        return source
    try:
        metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        metadata = None
    if isinstance(metadata, dict) and isinstance(metadata.get("data_file"), str):
        name = Path(metadata["data_file"])
        # A run's data file must belong to that folder, not an external source copy.
        if len(name.parts) == 1 and not name.is_absolute():
            measurement = source / name
            if measurement.is_file():
                return measurement
    for name in ("measurement.csv", "measurement.txt"):
        measurement = source / name
        if measurement.is_file():
            return measurement
    raise ValueError(f"{source}: run folder has no measurement.csv or measurement.txt")


def _looks_like_legacy_measurement(path: Path) -> bool:
    """Probe a bounded header; exclude prose/logs from collection review queues."""
    if path.suffix.casefold() not in {".txt", ".dat", ".csv"}:
        return False
    if path.name.casefold() in _SUPPORT_FILES:
        return False
    with path.open(encoding="utf-8-sig", errors="replace") as stream:
        lines = stream.read(8192).splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if path.suffix.casefold() == ".csv":
            fields = next(csv.reader([line]))
        else:
            fields = re.split(r"\s+", line)
        names = {field.strip().casefold() for field in fields}
        if {"measured_current_ma", "resistance_ohm"} <= names:
            return True
        if {"ireal_ma", "resistance_ohm"} <= names:
            return True
        if len(fields) != 3:
            return False
        try:
            return all(math.isfinite(float(field.replace(",", "."))) for field in fields)
        except ValueError:
            return False
    return False


def discover_measurements(root: str | Path, *, recursive: bool = False) -> list[Path]:
    """Find canonical runs and flat legacy files, optionally including nested groups.

    The default includes only the selected directory and its direct children.
    Canonical run folders contribute one measurement and are not traversed further.
    Directory links and administrative folders are never followed.
    """
    root = Path(root)
    found: list[Path] = []

    def visit(folder: Path, depth: int) -> None:
        try:
            measurement = resolve_measurement_path(folder)
        except ValueError:
            measurement = None
        if measurement is not None:
            found.append(measurement)
            return
        for child in sorted(folder.iterdir(), key=lambda path: path.name.casefold()):
            if child.is_symlink() or child.is_junction():
                continue
            if child.is_file() and _looks_like_legacy_measurement(child):
                found.append(child)
            elif child.is_dir() and (recursive or depth == 0):
                if child.name.casefold() not in _SUPPORT_FOLDERS and not child.name.startswith("."):
                    visit(child, depth + 1)

    visit(root, 0)
    return list(dict.fromkeys(found))
