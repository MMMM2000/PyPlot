from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from PyQt6 import QtCore


def _storage_root() -> Path:
    override = os.environ.get("MICROWIRE_BUILDER_STORAGE_ROOT", "").strip()
    if override:
        root = Path(override).expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return root
    candidate = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.StandardLocation.AppDataLocation
    )
    if not candidate:
        return Path.home() / ".microwire_data_builder"
    root = Path(candidate)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        return Path.home() / ".microwire_data_builder"
    return root


def _clone_table(table: Any) -> pd.DataFrame:
    if not isinstance(table, pd.DataFrame):
        return pd.DataFrame()
    try:
        return table.copy()
    except Exception:
        columns = list(table.columns)
        rows: List[Dict[Any, Any]] = []
        for row_idx in range(len(table.index)):
            record: Dict[Any, Any] = {}
            for col_idx, column in enumerate(columns):
                try:
                    record[column] = table.iat[row_idx, col_idx]
                except Exception:
                    record[column] = None
            rows.append(record)
        cloned = pd.DataFrame(rows, columns=columns)
        try:
            cloned.index = pd.Index(list(table.index))
        except Exception:
            pass
        return cloned


@dataclass
class MiniDatabaseData:
    """Persisted state for a single mini database section."""

    sources: List[str] = field(default_factory=list)
    processed: Dict[str, float] = field(default_factory=dict)
    table: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())
    extra: Dict[str, Any] = field(default_factory=dict)


class MiniDatabaseStore:
    """Load and save ``MiniDatabaseData`` records for individual sections."""

    _memory_data: Dict[str, MiniDatabaseData] = {}
    _memory_payloads: Dict[tuple[str, str], Any] = {}
    _disk_writes_suspended: int = 0
    _pending_sections: set[str] = set()
    _pending_payloads: set[tuple[str, str]] = set()

    def __init__(self, section: str) -> None:
        self.section = section
        base = _storage_root() / "mini_databases"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._base_dir = base
        self._meta_path = base / f"{section}.json"
        self._table_path = base / f"{section}.pkl"
        self._payload_dir = base / "payloads"
        try:
            self._payload_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    @property
    def meta_path(self) -> Path:
        return self._meta_path

    @property
    def table_path(self) -> Path:
        return self._table_path

    @classmethod
    @contextmanager
    def suspend_disk_writes(cls):
        cls._disk_writes_suspended += 1
        try:
            yield
        finally:
            cls._disk_writes_suspended = max(0, cls._disk_writes_suspended - 1)
            if cls._disk_writes_suspended == 0:
                cls._flush_pending_writes()

    @classmethod
    def _flush_pending_writes(cls) -> None:
        pending_sections = sorted(cls._pending_sections)
        pending_payloads = sorted(cls._pending_payloads)
        cls._pending_sections.clear()
        cls._pending_payloads.clear()

        for section in pending_sections:
            data = cls._memory_data.get(section)
            if isinstance(data, MiniDatabaseData):
                cls(section)._write_data_to_disk(data)

        for section, name in pending_payloads:
            cache_key = (section, name)
            if cache_key not in cls._memory_payloads:
                continue
            cls(section)._write_payload_to_disk(name, cls._memory_payloads[cache_key])

    def load(self) -> MiniDatabaseData:
        cached = self._memory_data.get(self.section)
        if isinstance(cached, MiniDatabaseData):
            return MiniDatabaseData(
                sources=list(cached.sources),
                processed=dict(cached.processed),
                table=_clone_table(cached.table),
                extra=dict(cached.extra),
            )
        data = MiniDatabaseData()
        if self._meta_path.exists():
            try:
                payload = json.loads(self._meta_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            sources = payload.get("sources", [])
            if isinstance(sources, (list, tuple)):
                data.sources = [str(entry) for entry in sources if isinstance(entry, str)]
            processed = payload.get("processed", {})
            if isinstance(processed, dict):
                for path, timestamp in processed.items():
                    try:
                        data.processed[str(path)] = float(timestamp)
                    except (TypeError, ValueError):
                        continue
            extra = payload.get("extra", {})
            if isinstance(extra, dict):
                data.extra = dict(extra)
        if self._table_path.exists():
            try:
                table = pd.read_pickle(self._table_path)
            except Exception:
                table = pd.DataFrame()
            if isinstance(table, pd.DataFrame):
                data.table = table
        self._memory_data[self.section] = MiniDatabaseData(
            sources=list(data.sources),
            processed=dict(data.processed),
            table=_clone_table(data.table),
            extra=dict(data.extra),
        )
        return data

    def save(self, data: MiniDatabaseData) -> None:
        self._memory_data[self.section] = MiniDatabaseData(
            sources=list(dict.fromkeys(data.sources)),
            processed=dict(data.processed),
            table=_clone_table(data.table),
            extra=dict(data.extra),
        )
        if self._disk_writes_suspended:
            self._pending_sections.add(self.section)
            return
        self._write_data_to_disk(data)

    def _write_data_to_disk(self, data: MiniDatabaseData) -> None:
        meta = {
            "sources": list(dict.fromkeys(data.sources)),
            "processed": data.processed,
            "extra": data.extra,
        }
        self._meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _clone_table(data.table).to_pickle(self._table_path)

    def clear_table(self) -> None:
        cached = self._memory_data.get(self.section)
        if isinstance(cached, MiniDatabaseData):
            cached.table = pd.DataFrame()
        try:
            self._table_path.unlink()
        except FileNotFoundError:
            pass

    def payload_path(self, name: str) -> Path:
        safe = name.replace(os.sep, "_").replace("..", "_")
        return self._payload_dir / f"{self.section}_{safe}.pkl"

    def save_payload(self, name: str, payload: Any) -> Path:
        path = self.payload_path(name)
        self._memory_payloads[(self.section, name)] = payload
        if self._disk_writes_suspended:
            self._pending_payloads.add((self.section, name))
            return path
        self._write_payload_to_disk(name, payload)
        return path

    def _write_payload_to_disk(self, name: str, payload: Any) -> Path:
        path = self.payload_path(name)
        pd.to_pickle(payload, path)
        return path

    def load_payload(self, name: str) -> Any:
        cache_key = (self.section, name)
        if cache_key in self._memory_payloads:
            return self._memory_payloads[cache_key]
        path = self.payload_path(name)
        if path.exists():
            try:
                payload = pd.read_pickle(path)
                self._memory_payloads[cache_key] = payload
                return payload
            except Exception:
                return None
        return None

    def clear_payload(self, name: str) -> None:
        self._memory_payloads.pop((self.section, name), None)
        self._pending_payloads.discard((self.section, name))
        path = self.payload_path(name)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


__all__ = ["MiniDatabaseData", "MiniDatabaseStore"]
