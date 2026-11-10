"""Read-only sample suggestions with bindings suitable for later reviewed import.

This module deliberately reads Builder tables only. It never opens embedded
measurement payloads, changes a project, or treats an existing diameter as a
calibration reference.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable, Mapping


@dataclass(frozen=True)
class SampleChoice:
    composition: str
    microwire: str
    source_kind: str
    source_path: str
    source_sha256: str
    source_rows: tuple[dict[str, object], ...] = ()
    brittle: bool | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wire_display(value: object) -> str:
    text = str(value if value is not None else "").strip()
    match = re.fullmatch(r"(\d+)\s*[/_\\-]\s*(\d+)", text)
    if match:
        return f"{int(match[1])}/{int(match[2])}"
    # Preserve unfamiliar specimen labels rather than stripping their identity.
    return text


def _sort_key(choice: SampleChoice) -> tuple[object, ...]:
    match = re.fullmatch(r"(\d+)/(\d+)", choice.microwire)
    return (
        choice.composition.casefold(),
        0 if match else 1,
        int(match[1]) if match else 0,
        int(match[2]) if match else 0,
        choice.microwire.casefold(),
    )


def _composition_token(value: str) -> str:
    """Match TMA's composition-folder tokens without broad punctuation folding."""
    return str(value or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def _row_brittle(row: Mapping[str, object]) -> bool | None:
    """Read an explicit annotation; blank legacy cells do not mean false."""
    override = row.get("_brittle_override")
    if isinstance(override, bool):
        return override
    value = row.get("Brittle")
    if isinstance(value, str) and value.strip().casefold() == "brittle":
        return True
    return None


def _check_cancel(cancel_callback: Callable[[], bool] | None) -> None:
    if cancel_callback is not None and cancel_callback():
        from microwire_data_builder.core import BuildCancelledError
        raise BuildCancelledError()


def list_fabrication_compositions(
    root: str | Path, *, cancel_callback: Callable[[], bool] | None = None,
) -> list[str]:
    """List immediate composition-folder suggestions without reading workbooks."""
    _check_cancel(cancel_callback)
    target = Path(root).expanduser().resolve(strict=True)
    if not target.is_dir():
        raise ValueError("Select a fabrication folder.")
    values = []
    for child in target.iterdir():
        _check_cancel(cancel_callback)
        if child.is_dir() and not child.name.startswith((".", "~")) and child.name.strip():
            values.append(child.name.strip())
    return sorted(values, key=str.casefold)


def _fabrication_composition_folder(
    root: Path, composition: str, cancel_callback: Callable[[], bool] | None,
) -> Path:
    key = _composition_token(composition)
    if not key:
        raise ValueError("Select a composition before loading its fabrication workbooks.")
    _check_cancel(cancel_callback)
    if _composition_token(root.name) == key:
        return root
    matches = []
    for child in root.iterdir():
        _check_cancel(cancel_callback)
        if (child.is_dir() and not child.name.startswith((".", "~"))
                and _composition_token(child.name) == key):
            matches.append(child)
    if not matches:
        raise ValueError(f"No fabrication folder matched composition {composition}.")
    if len(matches) > 1:
        raise ValueError(f"Multiple fabrication folders matched composition {composition}; choose a specific composition folder.")
    return matches[0]


def load_project_samples(path: str | Path) -> list[SampleChoice]:
    """Return sample choices through the normal Builder package reader.

    Duplicate samples across sections remain one dropdown choice, while every
    section/row binding is retained. Row indexes are zero-based. The SHA binds
    the identities to the exact source bytes, and a concurrent edit is rejected.
    Brittle is unknown when no explicit value exists or sections disagree; a
    blank historical cell is not treated as an explicit non-brittle assessment.
    Packaged projects read table entries only: unrelated section state and
    measurement payloads are not needed for sample selection.
    """
    from microwire_data_builder import project_package

    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_file():
        raise ValueError("Select a Builder project file.")
    before = _sha256(target)
    if project_package.is_project_package(target):
        index = project_package.inspect_project_package(target)
        payload = index.project_header()
        with index.open_reader() as reader:
            for section_key, descriptor in index.sections.items():
                table = project_package._read_json_bytes(  # noqa: SLF001
                    reader.read_entry(descriptor["table"]), f"{section_key} table")
                project_package._validate_tree_limits(table)  # noqa: SLF001
                if (not isinstance(table, dict) or set(table) != {"columns", "rows", "index"}
                        or not isinstance(table["rows"], list)):
                    raise project_package.SafeCodecError(
                        f"Builder package table schema is invalid: {section_key}")
                payload["sections"][section_key] = {"rows": table["rows"]}
    else:
        payload = project_package.load_project_table_projection(target)
    if _sha256(target) != before:
        raise ValueError("The Builder project changed while reading samples. Reload it.")
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    brittle_values: dict[tuple[str, str], set[bool]] = {}

    def collect(section: str, rows: object) -> None:
        if not isinstance(rows, list):
            return
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            composition = str(row.get("Composition") or "").strip()
            raw_wire = row.get("Microwire")
            if raw_wire is None or not str(raw_wire).strip():
                raw_wire = row.get("Wire")
            microwire = _wire_display(raw_wire)
            if not composition or not microwire:
                continue
            key = (composition, microwire)
            annotation = _row_brittle(row)
            if annotation is not None:
                brittle_values.setdefault(key, set()).add(annotation)
            grouped.setdefault(key, []).append({
                "binding_type": "project_table_row",
                "section": section,
                "row_index": row_index,
                "composition": composition,
                "microwire": microwire,
                "source_microwire": str(raw_wire),
            })

    sections = payload.get("sections", {})
    if isinstance(sections, Mapping):
        for section, value in sections.items():
            if isinstance(value, Mapping):
                collect(str(section), value.get("rows"))
    collect("", payload.get("rows"))
    choices = []
    for (composition, microwire), rows in grouped.items():
        values = brittle_values.get((composition, microwire), set())
        brittle = next(iter(values)) if len(values) == 1 else None
        choices.append(SampleChoice(composition, microwire, "project", str(target), before,
                                    tuple(rows), brittle))
    return sorted(choices, key=_sort_key)


def load_fabrication_samples(
    root: str | Path,
    *,
    composition: str | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> list[SampleChoice]:
    """Read fabrication piece keys using Builder's existing workbook parser.

    Each contributing workbook is indexed separately so its exact path and SHA
    remain available even when multiple workbooks mention the same sample.
    ``source_sha256`` identifies the contributing workbook inventory; the
    individual workbook SHA values are in ``source_rows``. With ``composition``,
    only that immediate composition folder (or matching root) is traversed, like
    TMA's lazy loading. A missing or ambiguous match never falls back to a whole
    production-root scan. The existing parser
    does not expose physical Excel row coordinates, so only verified piece keys
    are recorded rather than inventing row numbers.
    """
    from microwire_data_builder.core import build_fabrication_index

    _check_cancel(cancel_callback)
    target = Path(root).expanduser().resolve(strict=True)
    if not target.is_dir():
        raise ValueError("Select a fabrication folder.")
    search = target if composition is None else _fabrication_composition_folder(target, composition, cancel_callback)
    files = []
    for directory, directories, filenames in os.walk(search):
        _check_cancel(cancel_callback)
        directories.sort(key=str.casefold)
        for name in sorted(filenames, key=str.casefold):
            _check_cancel(cancel_callback)
            if name.lower().endswith(".xlsx") and not name.startswith("~$"):
                path = Path(directory) / name
                if path.is_file():
                    files.append(path)
    if composition is not None and not files:
        raise ValueError(f"No fabrication Excel workbooks were found for {composition}.")
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for workbook in files:
        _check_cancel(cancel_callback)
        before = _sha256(workbook)
        index = build_fabrication_index([workbook], cancel_callback=cancel_callback)
        if _sha256(workbook) != before:
            raise ValueError(f"Fabrication workbook changed while reading samples: {workbook}")
        for (composition, draw, piece), data in index.piece_level.items():
            if not isinstance(data, Mapping) or not str(composition).strip():
                continue
            composition = str(composition).strip()
            microwire = f"{int(draw)}/{int(piece)}"
            grouped.setdefault((composition, microwire), []).append({
                "binding_type": "fabrication_piece_key",
                "workbook": str(workbook.resolve()),
                "workbook_sha256": before,
                "composition": composition,
                "draw": int(draw),
                "piece": int(piece),
            })
    choices = []
    for (composition, microwire), rows in grouped.items():
        inventory = sorted({(str(row["workbook"]), str(row["workbook_sha256"])) for row in rows})
        inventory_sha = hashlib.sha256(json.dumps(inventory, separators=(",", ":")).encode()).hexdigest()
        choices.append(SampleChoice(composition, microwire, "fabrication", str(target),
                                    inventory_sha, tuple(rows)))
    return sorted(choices, key=_sort_key)


__all__ = ["SampleChoice", "load_project_samples", "load_fabrication_samples", "list_fabrication_compositions"]
