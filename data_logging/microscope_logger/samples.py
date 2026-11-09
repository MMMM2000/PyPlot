"""Read-only sample suggestions with bindings suitable for later reviewed import.

This module deliberately reads Builder tables only. It never opens embedded
measurement payloads, changes a project, or treats an existing diameter as a
calibration reference.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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


def load_project_samples(path: str | Path) -> list[SampleChoice]:
    """Return sample choices from the same safe table projection used by TMA.

    Duplicate samples across sections remain one dropdown choice, while every
    section/row binding is retained. Row indexes are zero-based. The SHA binds
    the identities to the exact source bytes, and a concurrent edit is rejected.
    """
    from microwire_data_builder.project_package import load_project_table_projection

    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_file():
        raise ValueError("Select a Builder project file.")
    before = _sha256(target)
    payload = load_project_table_projection(target)
    if _sha256(target) != before:
        raise ValueError("The Builder project changed while reading samples. Reload it.")
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}

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
            grouped.setdefault((composition, microwire), []).append({
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
    return sorted([
        SampleChoice(composition, microwire, "project", str(target), before, tuple(rows))
        for (composition, microwire), rows in grouped.items()
    ], key=_sort_key)


def load_fabrication_samples(
    root: str | Path,
    *,
    cancel_callback: Callable[[], bool] | None = None,
) -> list[SampleChoice]:
    """Read fabrication piece keys using Builder's existing workbook parser.

    Each contributing workbook is indexed separately so its exact path and SHA
    remain available even when multiple workbooks mention the same sample.
    ``source_sha256`` identifies the contributing workbook inventory; the
    individual workbook SHA values are in ``source_rows``. The existing parser
    does not expose physical Excel row coordinates, so only verified piece keys
    are recorded rather than inventing row numbers.
    """
    from microwire_data_builder.core import BuildCancelledError, build_fabrication_index

    target = Path(root).expanduser().resolve(strict=True)
    if not target.is_dir():
        raise ValueError("Select a fabrication folder.")
    files = sorted((p for p in target.rglob("*.xlsx")
                    if p.is_file() and not p.name.startswith("~$")),
                   key=lambda p: str(p).casefold())
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for workbook in files:
        if cancel_callback is not None and cancel_callback():
            raise BuildCancelledError()
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


__all__ = ["SampleChoice", "load_project_samples", "load_fabrication_samples"]
