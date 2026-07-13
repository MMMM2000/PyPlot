from __future__ import annotations

import copy
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
    _pending_section_values: Dict[str, MiniDatabaseData] = {}
    _pending_payload_values: Dict[tuple[str, str], Any] = {}
    _memory_transactions: List["_MiniDatabaseMemoryTransaction"] = []
    _discard_writes_depth: int = 0

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
    def begin_memory_transaction(cls) -> "_MiniDatabaseMemoryTransaction":
        if not cls._memory_transactions:
            if cls._disk_writes_suspended:
                raise RuntimeError("Cannot start a project transaction while disk writes are suspended")
            # Pending keys historically pointed straight at the mutable base
            # cache. Freeze any legacy entries before a transaction can replace
            # that cache with a newly loaded project.
            for section in cls._pending_sections:
                data = cls._memory_data.get(section)
                if section not in cls._pending_section_values and isinstance(
                    data, MiniDatabaseData
                ):
                    cls._pending_section_values[section] = MiniDatabaseData(
                        sources=list(data.sources),
                        processed=dict(data.processed),
                        table=_clone_table(data.table),
                        extra=copy.deepcopy(data.extra),
                    )
            for key in cls._pending_payloads:
                if key not in cls._pending_payload_values and key in cls._memory_payloads:
                    cls._pending_payload_values[key] = copy.deepcopy(cls._memory_payloads[key])
        return _MiniDatabaseMemoryTransaction(cls)

    @classmethod
    @contextmanager
    def discard_writes(cls):
        cls._discard_writes_depth += 1
        try:
            yield
        finally:
            cls._discard_writes_depth = max(0, cls._discard_writes_depth - 1)

    @classmethod
    def _flush_pending_writes(cls) -> None:
        pending_sections = sorted(cls._pending_sections)
        pending_payloads = sorted(cls._pending_payloads)
        cls._pending_sections.clear()
        cls._pending_payloads.clear()

        for section in pending_sections:
            data = cls._pending_section_values.pop(section, None)
            if not isinstance(data, MiniDatabaseData):
                data = cls._memory_data.get(section)
            if isinstance(data, MiniDatabaseData):
                cls(section)._write_data_to_disk(data)

        for section, name in pending_payloads:
            cache_key = (section, name)
            payload = cls._pending_payload_values.pop(cache_key, _TRANSACTION_MISSING)
            if payload is _TRANSACTION_MISSING:
                payload = cls._memory_payloads.get(cache_key, _TRANSACTION_MISSING)
            if payload is _TRANSACTION_MISSING:
                continue
            cls(section)._write_payload_to_disk(name, payload)

    def load(self) -> MiniDatabaseData:
        cached: Any = _TRANSACTION_MISSING
        if self._memory_transactions:
            cached = self._memory_transactions[-1].lookup_data(self.section)
        if cached is _TRANSACTION_MISSING:
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
        if not self._discard_writes_depth and not self._memory_transactions:
            self._memory_data[self.section] = MiniDatabaseData(
                sources=list(data.sources),
                processed=dict(data.processed),
                table=_clone_table(data.table),
                extra=dict(data.extra),
            )
        return data

    def save(self, data: MiniDatabaseData) -> None:
        if self._discard_writes_depth:
            return
        cached = MiniDatabaseData(
            sources=list(dict.fromkeys(data.sources)),
            processed=dict(data.processed),
            table=_clone_table(data.table),
            extra=dict(data.extra),
        )
        if self._memory_transactions:
            self._memory_transactions[-1].save_data(self.section, cached)
            return
        self._memory_data[self.section] = cached
        if self._disk_writes_suspended:
            self._pending_sections.add(self.section)
            self._pending_section_values[self.section] = MiniDatabaseData(
                sources=list(cached.sources),
                processed=dict(cached.processed),
                table=_clone_table(cached.table),
                extra=copy.deepcopy(cached.extra),
            )
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
        if self._discard_writes_depth:
            return
        if self._memory_transactions:
            data = self.load()
            data.table = pd.DataFrame()
            self.save(data)
            return
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
        if self._discard_writes_depth:
            return path
        cache_key = (self.section, name)
        if self._memory_transactions:
            self._memory_transactions[-1].save_payload(cache_key, payload)
            return path
        self._memory_payloads[cache_key] = payload
        if self._disk_writes_suspended:
            self._pending_payloads.add((self.section, name))
            self._pending_payload_values[cache_key] = copy.deepcopy(payload)
            return path
        self._write_payload_to_disk(name, payload)
        return path

    def _write_payload_to_disk(self, name: str, payload: Any) -> Path:
        path = self.payload_path(name)
        pd.to_pickle(payload, path)
        return path

    def load_payload(self, name: str) -> Any:
        cache_key = (self.section, name)
        if self._memory_transactions:
            cached = self._memory_transactions[-1].lookup_payload(cache_key)
            if cached is not _TRANSACTION_MISSING:
                return cached
        if cache_key in self._memory_payloads:
            return self._memory_payloads[cache_key]
        path = self.payload_path(name)
        if path.exists():
            try:
                payload = pd.read_pickle(path)
                if not self._discard_writes_depth and not self._memory_transactions:
                    self._memory_payloads[cache_key] = payload
                return payload
            except Exception:
                return None
        return None

    def clear_payload(self, name: str) -> None:
        if self._discard_writes_depth:
            return
        if self._memory_transactions:
            self._memory_transactions[-1].clear_payload((self.section, name))
            return
        self._memory_payloads.pop((self.section, name), None)
        self._pending_payloads.discard((self.section, name))
        self._pending_payload_values.pop((self.section, name), None)
        path = self.payload_path(name)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


_TRANSACTION_MISSING = object()


class _MiniDatabaseMemoryTransaction:
    """Copy-on-write, memory-only transaction for project restoration."""

    def __init__(self, store_cls: type[MiniDatabaseStore]) -> None:
        self._store_cls = store_cls
        self._data_updates: Dict[str, MiniDatabaseData] = {}
        self._payload_updates: Dict[tuple[str, str], Any] = {}
        self._payload_deletes: set[tuple[str, str]] = set()
        self._payload_hidden_sections: set[str] = set()
        self._finished = False
        store_cls._memory_transactions.append(self)

    def _parent(self) -> "_MiniDatabaseMemoryTransaction | None":
        stack = self._store_cls._memory_transactions
        if self not in stack:
            return None
        index = stack.index(self)
        return stack[index - 1] if index > 0 else None

    def lookup_data(self, section: str) -> Any:
        if section in self._data_updates:
            return self._data_updates[section]
        parent = self._parent()
        return parent.lookup_data(section) if parent is not None else _TRANSACTION_MISSING

    def save_data(self, section: str, data: MiniDatabaseData) -> None:
        self._data_updates[section] = data

    def lookup_payload(self, key: tuple[str, str]) -> Any:
        if key in self._payload_deletes:
            return None
        if key in self._payload_updates:
            return self._payload_updates[key]
        if key[0] in self._payload_hidden_sections:
            return None
        parent = self._parent()
        return parent.lookup_payload(key) if parent is not None else _TRANSACTION_MISSING

    def save_payload(self, key: tuple[str, str], payload: Any) -> None:
        self._payload_deletes.discard(key)
        self._payload_updates[key] = payload

    def clear_payload(self, key: tuple[str, str]) -> None:
        self._payload_updates.pop(key, None)
        self._payload_deletes.add(key)

    def clear_section_payloads(self, section: str) -> None:
        self._payload_hidden_sections.add(section)
        for key in list(self._payload_updates):
            if key[0] == section:
                self._payload_updates.pop(key, None)
        self._payload_deletes.update(
            key for key in self._store_cls._memory_payloads if key[0] == section
        )

    def _finish(self) -> None:
        cls = self._store_cls
        if not cls._memory_transactions or cls._memory_transactions[-1] is not self:
            raise RuntimeError("Project memory transactions must finish in stack order")
        cls._memory_transactions.pop()
        self._finished = True

    def rollback(self) -> None:
        if self._finished:
            return
        self._finish()

    def commit_memory_only(self) -> None:
        if self._finished:
            return
        cls = self._store_cls
        parent = self._parent()
        if parent is not None:
            parent._data_updates.update(self._data_updates)
            for section in self._payload_hidden_sections:
                parent.clear_section_payloads(section)
            for key in self._payload_deletes:
                parent.clear_payload(key)
            for key, payload in self._payload_updates.items():
                parent.save_payload(key, payload)
        else:
            cls._memory_data.update(self._data_updates)
            if self._payload_hidden_sections:
                for key in list(cls._memory_payloads):
                    if key[0] in self._payload_hidden_sections:
                        cls._memory_payloads.pop(key, None)
            for key in self._payload_deletes:
                cls._memory_payloads.pop(key, None)
            cls._memory_payloads.update(self._payload_updates)
        self._finish()

    @property
    def finished(self) -> bool:
        return self._finished


__all__ = ["MiniDatabaseData", "MiniDatabaseStore"]
