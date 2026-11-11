"""Physical-piece identity and a workstation-local, explicit sample registry.

This database is NOT an allocation authority across Drive-synchronised machines.
Exchange records with ``merge``; conflicts remain visible and are never renumbered.
No database is opened until a caller supplies a path. Opening/reading never reserves
an ID. Legacy measurements must be imported explicitly, not guessed from filenames.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import re
import sqlite3
import json
from uuid import uuid4


def sample_number(label: str) -> int | None:
    """Return positive canonical sN number; legacy text remains uninterpreted."""
    return int(label[1:]) if re.fullmatch(r"s[1-9][0-9]*", label) else None


def wire_key(wire: str) -> str:
    """Unify the loggers' numeric display/filename forms without guessing aliases."""
    value = wire.strip()
    match = re.fullmatch(r"([0-9]+)\s*[/_\\-]\s*([0-9]+)", value)
    return f"{int(match[1])}/{int(match[2])}" if match else value


@dataclass(frozen=True)
class PhysicalSample:
    uid: str
    composition: str
    wire: str
    label: str
    source: str
    parent_uid: str | None = None


class IdentityConflict(ValueError):
    """A label or immutable identity has incompatible physical-piece claims."""


class SampleRegistry:
    """Local registry. Callers must show that coverage is local, not global.

    One row describes one physical piece; measurements reference its uid. Reuse
    uses the same row. New pieces are explicitly registered, never on text edits.
    SQLite serialises writers on one workstation; do not place this DB in Drive.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS samples (
                uid TEXT PRIMARY KEY, composition TEXT NOT NULL,
                wire TEXT NOT NULL, label TEXT NOT NULL, source TEXT NOT NULL,
                parent_uid TEXT)""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def known(self, composition: str, wire: str) -> tuple[PhysicalSample, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM samples WHERE composition=? AND wire=? ORDER BY label, uid",
                (composition.strip(), wire_key(wire)),
            ).fetchall()
        return tuple(PhysicalSample(*row) for row in rows)

    def suggested_label(self, composition: str, wire: str) -> str:
        """Next locally observed number, NOT a reservation or global guarantee."""
        numbers = [sample_number(row.label) or 0 for row in self.known(composition, wire)]
        return f"s{max(numbers, default=0) + 1}"

    def conflicts(self, composition: str, wire: str) -> dict[str, tuple[PhysicalSample, ...]]:
        grouped: dict[str, list[PhysicalSample]] = {}
        for row in self.known(composition, wire):
            grouped.setdefault(row.label, []).append(row)
        return {label: tuple(rows) for label, rows in grouped.items() if len(rows) > 1}

    def snapshot(self) -> str:
        """Portable immutable records, not a global allocation lease or counter."""
        with self._connect() as db:
            rows = db.execute("SELECT * FROM samples ORDER BY uid").fetchall()
        return json.dumps({"schema_version": 1, "application": "PyPlot physical samples",
                           "records": [asdict(PhysicalSample(*row)) for row in rows]},
                          ensure_ascii=False, indent=2)

    @staticmethod
    def parse_snapshot(text: str) -> tuple[PhysicalSample, ...]:
        """Validate an explicitly selected exchange file without touching storage."""
        if len(text) > 5_000_000:
            raise ValueError("Sample exchange exceeds the 5 MB limit")
        payload = json.loads(text)
        if (not isinstance(payload, dict) or payload.get("schema_version") != 1
                or payload.get("application") != "PyPlot physical samples"
                or not isinstance(payload.get("records"), list)):
            raise ValueError("Not a supported physical sample exchange")
        records = []
        keys = {"uid", "composition", "wire", "label", "source", "parent_uid"}
        for value in payload["records"]:
            if not isinstance(value, dict) or set(value) != keys:
                raise ValueError("Invalid sample exchange record")
            if any(not isinstance(value[key], str) or not value[key].strip()
                   for key in keys - {"parent_uid"}):
                raise ValueError("Sample exchange identity fields must be non-empty strings")
            if value["parent_uid"] is not None and not isinstance(value["parent_uid"], str):
                raise ValueError("Invalid parent sample identity")
            records.append(PhysicalSample(**value))
        return tuple(records)

    def create(self, composition: str, wire: str, label: str, *, source: str,
               parent_uid: str | None = None) -> PhysicalSample:
        """Explicitly register a new local piece, rejecting a stale suggestion."""
        composition, wire = composition.strip(), wire_key(wire)
        if not composition or not wire or sample_number(label) is None or not source.strip():
            raise ValueError("New pieces require composition, wire, positive sN and source")
        row = PhysicalSample(str(uuid4()), composition, wire, label, source, parent_uid)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM samples WHERE composition=? AND wire=? AND label=?",
                          (composition, wire, label)).fetchone():
                raise IdentityConflict("Sample ID already recorded locally; select the existing piece")
            if parent_uid is not None:
                parent = db.execute("SELECT composition, wire FROM samples WHERE uid=?", (parent_uid,)).fetchone()
                if parent != (composition, wire):
                    raise ValueError("Parent must be a known piece of the same composition and wire")
            self._insert(db, row)
        return row

    def merge(self, records: tuple[PhysicalSample, ...]) -> None:
        """Import explicit records atomically, preserving legacy labels and collisions.

        Same UUID must describe exactly the same record. Different UUIDs claiming
        the same label remain separate and appear in ``conflicts``. Sources should
        identify provenance, not be inferred from the logger currently in use.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in records:
                if not all((row.uid, row.composition.strip(), row.wire.strip(), row.label, row.source.strip())):
                    raise ValueError("Imported records require explicit identity and provenance")
                if (row.composition != row.composition.strip() or row.wire != wire_key(row.wire)):
                    raise ValueError("Composition and wire keys must be canonical (numeric wires use /)")
                existing = db.execute("SELECT * FROM samples WHERE uid=?", (row.uid,)).fetchone()
                values = (row.uid, row.composition, row.wire, row.label, row.source, row.parent_uid)
                if existing is not None:
                    if existing != values:
                        raise IdentityConflict("Immutable sample record changed; explicit reconciliation required")
                else:
                    self._insert(db, row)

    @staticmethod
    def _insert(db: sqlite3.Connection, row: PhysicalSample) -> None:
        db.execute("INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?)",
                   (row.uid, row.composition, row.wire, row.label, row.source, row.parent_uid))
