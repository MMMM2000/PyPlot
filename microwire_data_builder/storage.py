from __future__ import annotations

import copy
import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List

import pandas as pd
from PyQt6 import QtCore

from .safe_codec import (
    SafeCodecError,
    atomic_write_json,
    decode_envelope,
    encode_envelope,
    read_json_file,
)


LOGGER = logging.getLogger(__name__)


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
    _blocked_sections: set[str] = set()
    _blocked_payloads: set[tuple[str, str]] = set()
    _payload_loaders: Dict[tuple[str, str], Callable[[], Any]] = {}
    _payload_tombstones: set[tuple[str, str]] = set()

    def __init__(
        self,
        section: str,
        *,
        suppress_legacy_diagnostics: bool = False,
    ) -> None:
        self.section = section
        self._suppress_legacy_diagnostics = bool(suppress_legacy_diagnostics)
        base = _storage_root() / "mini_databases"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._base_dir = base
        self._meta_path = base / f"{section}.json"
        self._data_path = base / f"{section}.store.json"
        self._legacy_table_path = base / f"{section}.pkl"
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
        return self._data_path

    @property
    def legacy_table_path(self) -> Path:
        return self._legacy_table_path

    def _legacy_diagnostic(self, path: Path, label: str) -> str:
        message = (
            f"Legacy Builder {label} was not loaded from {path}; ordinary startup never "
            "executes pickle. Use the explicit trusted-copy migration command with a "
            "separate output location."
        )
        if not self._suppress_legacy_diagnostics:
            LOGGER.warning(message)
        return message

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
        if self._data_path.exists():
            try:
                decoded = decode_envelope(read_json_file(self._data_path))
                if not isinstance(decoded, dict):
                    raise SafeCodecError("Builder store root must decode to a mapping")
                sources = decoded.get("sources", [])
                processed = decoded.get("processed", {})
                table = decoded.get("table")
                extra = decoded.get("extra", {})
                if not isinstance(sources, list) or not all(
                    isinstance(entry, str) for entry in sources
                ):
                    raise SafeCodecError("Builder store sources must be a list of strings")
                if not isinstance(processed, dict) or not all(
                    isinstance(path, str)
                    and isinstance(timestamp, (int, float))
                    and not isinstance(timestamp, bool)
                    for path, timestamp in processed.items()
                ):
                    raise SafeCodecError(
                        "Builder store processed must map strings to numeric timestamps"
                    )
                if not isinstance(table, pd.DataFrame):
                    raise SafeCodecError("Builder store table must be a pandas DataFrame")
                if not isinstance(extra, dict):
                    raise SafeCodecError("Builder store extra must be a mapping")
                data.sources = list(sources)
                data.processed = {
                    path: float(timestamp) for path, timestamp in processed.items()
                }
                data.table = table
                data.extra = extra
                self._blocked_sections.discard(self.section)
            except Exception as exc:
                message = f"Failed to decode safe Builder store {self._data_path}: {exc}"
                LOGGER.error(message)
                self._blocked_sections.add(self.section)
                raise SafeCodecError(message) from exc
        elif self._meta_path.exists():
            # Version-1 metadata was plain JSON and can be recovered safely.  Its
            # companion DataFrame pickle remains blocked.
            try:
                payload = read_json_file(self._meta_path)
                if not isinstance(payload, dict):
                    raise SafeCodecError("Legacy Builder metadata root must be an object")
                sources = payload.get("sources", [])
                processed = payload.get("processed", {})
                extra = payload.get("extra", {})
                if not isinstance(sources, list) or not all(
                    isinstance(entry, str) for entry in sources
                ):
                    raise SafeCodecError("Legacy Builder sources must be a list of strings")
                if not isinstance(processed, dict):
                    raise SafeCodecError("Legacy Builder processed must be a mapping")
                if not isinstance(extra, dict):
                    raise SafeCodecError("Legacy Builder extra must be a mapping")
                data.sources = list(sources)
                for path, timestamp in processed.items():
                    if not isinstance(path, str) or isinstance(timestamp, bool):
                        raise SafeCodecError("Legacy Builder processed entry is malformed")
                    try:
                        data.processed[path] = float(timestamp)
                    except (TypeError, ValueError) as exc:
                        raise SafeCodecError(
                            "Legacy Builder processed timestamp is malformed"
                        ) from exc
                data.extra = dict(extra)
            except Exception as exc:
                message = f"Failed to read legacy Builder metadata {self._meta_path}: {exc}"
                LOGGER.error(message)
                self._blocked_sections.add(self.section)
                raise SafeCodecError(message) from exc
            if self._legacy_table_path.exists():
                self._legacy_diagnostic(self._legacy_table_path, "table")
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
        if self.section in self._blocked_sections:
            raise SafeCodecError(
                f"Builder store {self._data_path} is blocked after a decode failure; "
                "quarantine or repair it explicitly before saving"
            )
        cached = MiniDatabaseData(
            sources=list(dict.fromkeys(data.sources)),
            processed=dict(data.processed),
            table=_clone_table(data.table),
            extra=dict(data.extra),
        )
        if self._memory_transactions:
            self._memory_transactions[-1].save_data(self.section, cached)
            return
        if self._disk_writes_suspended:
            self._memory_data[self.section] = cached
            self._pending_sections.add(self.section)
            self._pending_section_values[self.section] = MiniDatabaseData(
                sources=list(cached.sources),
                processed=dict(cached.processed),
                table=_clone_table(cached.table),
                extra=copy.deepcopy(cached.extra),
            )
            return
        self._write_data_to_disk(data)
        self._memory_data[self.section] = cached

    def _write_data_to_disk(self, data: MiniDatabaseData) -> None:
        stored = {
            "sources": list(dict.fromkeys(data.sources)),
            "processed": data.processed,
            "extra": data.extra,
            "table": _clone_table(data.table),
        }
        atomic_write_json(self._data_path, encode_envelope(stored))

    def clear_table(self) -> None:
        if self._discard_writes_depth:
            return
        data = self.load()
        data.table = pd.DataFrame()
        self.save(data)

    def payload_path(self, name: str) -> Path:
        safe = str(name)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", safe) or safe in {".", ".."}:
            raise ValueError(f"Invalid Builder payload name: {name!r}")
        path = self._payload_dir / f"{self.section}_{safe}.json"
        if path.resolve().parent != self._payload_dir.resolve():
            raise ValueError("Builder payload path escaped its storage directory")
        return path

    def legacy_payload_path(self, name: str) -> Path:
        safe = str(name)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", safe) or safe in {".", ".."}:
            raise ValueError(f"Invalid Builder payload name: {name!r}")
        path = self._payload_dir / f"{self.section}_{safe}.pkl"
        if path.resolve().parent != self._payload_dir.resolve():
            raise ValueError("Builder legacy payload path escaped its storage directory")
        return path

    def save_payload(self, name: str, payload: Any) -> Path:
        path = self.payload_path(name)
        if self._discard_writes_depth:
            return path
        cache_key = (self.section, name)
        if self._memory_transactions:
            self._memory_transactions[-1].clear_payload_loader(cache_key)
        else:
            self._payload_loaders.pop(cache_key, None)
            self._payload_tombstones.discard(cache_key)
        if cache_key in self._blocked_payloads:
            raise SafeCodecError(
                f"Builder payload {path} is blocked after a decode failure; "
                "quarantine or repair it explicitly before saving"
            )
        if self._memory_transactions:
            self._memory_transactions[-1].save_payload(cache_key, payload)
            return path
        if self._disk_writes_suspended:
            self._memory_payloads[cache_key] = payload
            self._pending_payloads.add((self.section, name))
            self._pending_payload_values[cache_key] = copy.deepcopy(payload)
            return path
        self._write_payload_to_disk(name, payload)
        self._memory_payloads[cache_key] = payload
        return path

    def _write_payload_to_disk(self, name: str, payload: Any) -> Path:
        path = self.payload_path(name)
        atomic_write_json(path, encode_envelope(payload))
        return path

    def load_payload(self, name: str) -> Any:
        cache_key = (self.section, name)
        if self._memory_transactions:
            cached = self._memory_transactions[-1].lookup_payload(cache_key)
            if cached is not _TRANSACTION_MISSING:
                return cached
        if cache_key in self._memory_payloads:
            return self._memory_payloads[cache_key]
        loader: Any = _TRANSACTION_MISSING
        if self._memory_transactions:
            loader = self._memory_transactions[-1].lookup_payload_loader(cache_key)
        if loader is _TRANSACTION_MISSING:
            loader = self._payload_loaders.get(cache_key, _TRANSACTION_MISSING)
        if callable(loader):
            try:
                payload = loader()
            except Exception as exc:
                raise SafeCodecError(
                    f"Failed to lazily load packaged Builder payload {self.section}.{name}: {exc}"
                ) from exc
            if self._memory_transactions:
                transaction = self._memory_transactions[-1]
                transaction.clear_payload_loader(cache_key)
                transaction.save_payload(cache_key, payload)
            elif not self._discard_writes_depth:
                self._payload_loaders.pop(cache_key, None)
                self._memory_payloads[cache_key] = payload
            return payload
        path = self.payload_path(name)
        if path.exists():
            try:
                payload = decode_envelope(read_json_file(path))
                if not self._discard_writes_depth and not self._memory_transactions:
                    self._memory_payloads[cache_key] = payload
                return payload
            except Exception as exc:
                message = f"Failed to decode safe Builder payload {path}: {exc}"
                LOGGER.error(message)
                self._blocked_payloads.add(cache_key)
                raise SafeCodecError(message) from exc
        legacy_path = self.legacy_payload_path(name)
        if legacy_path.exists():
            self._legacy_diagnostic(legacy_path, "payload")
        return None

    def clear_payload(self, name: str) -> None:
        if self._discard_writes_depth:
            return
        if self._memory_transactions:
            self._memory_transactions[-1].clear_payload((self.section, name))
            self._memory_transactions[-1].clear_payload_loader((self.section, name))
            return
        if (self.section, name) in self._blocked_payloads:
            raise SafeCodecError(
                "Blocked safe payload must be quarantined or repaired explicitly"
            )
        self._memory_payloads.pop((self.section, name), None)
        self._payload_loaders.pop((self.section, name), None)
        self._payload_tombstones.add((self.section, name))
        self._pending_payloads.discard((self.section, name))
        self._pending_payload_values.pop((self.section, name), None)
        path = self.payload_path(name)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def register_payload_loader(self, name: str, loader: Callable[[], Any]) -> None:
        """Register a data-only package resolver without materializing its payload."""

        self.payload_path(name)  # validate the identifier without touching disk
        if not callable(loader):
            raise TypeError("Builder payload loader must be callable")
        key = (self.section, name)
        if self._memory_transactions:
            self._memory_transactions[-1].register_payload_loader(key, loader)
        else:
            self._memory_payloads.pop(key, None)
            self._payload_loaders[key] = loader
            self._payload_tombstones.discard(key)

    def payload_tombstones(self) -> set[str]:
        """Return payload ids explicitly cleared from the current project."""

        return {
            name for section, name in self._payload_tombstones if section == self.section
        }

    def acknowledge_payload_tombstones(self, names: Iterable[str]) -> None:
        for name in names:
            self._payload_tombstones.discard((self.section, str(name)))

    def has_payload_loader(self, name: str) -> bool:
        key = (self.section, name)
        if self._memory_transactions:
            loader = self._memory_transactions[-1].lookup_payload_loader(key)
            if callable(loader):
                return True
            if loader is not _TRANSACTION_MISSING:
                return False
        return callable(self._payload_loaders.get(key))

    def quarantine_corrupt_store(self, destination: Path) -> Path:
        """Explicitly move a blocked safe store aside so a clean save may proceed."""

        if self.section not in self._blocked_sections:
            raise SafeCodecError("Builder store is not marked as corrupt")
        target = Path(destination)
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(self._data_path, target)
        self._data_path.unlink()
        self._blocked_sections.discard(self.section)
        return target

    def quarantine_corrupt_payload(self, name: str, destination: Path) -> Path:
        """Explicitly move a blocked safe payload aside before replacing it."""

        key = (self.section, name)
        if key not in self._blocked_payloads:
            raise SafeCodecError("Builder payload is not marked as corrupt")
        target = Path(destination)
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self.payload_path(name)
        os.link(source, target)
        source.unlink()
        self._blocked_payloads.discard(key)
        return target


_TRANSACTION_MISSING = object()


class _MiniDatabaseMemoryTransaction:
    """Copy-on-write, memory-only transaction for project restoration."""

    def __init__(self, store_cls: type[MiniDatabaseStore]) -> None:
        self._store_cls = store_cls
        self._data_updates: Dict[str, MiniDatabaseData] = {}
        self._payload_updates: Dict[tuple[str, str], Any] = {}
        self._payload_deletes: set[tuple[str, str]] = set()
        self._payload_loader_updates: Dict[tuple[str, str], Callable[[], Any]] = {}
        self._payload_loader_deletes: set[tuple[str, str]] = set()
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
        self.clear_payload_loader(key)
        self._payload_updates[key] = payload

    def clear_payload(self, key: tuple[str, str]) -> None:
        self._payload_updates.pop(key, None)
        self._payload_deletes.add(key)

    def lookup_payload_loader(self, key: tuple[str, str]) -> Any:
        if key in self._payload_loader_deletes or key in self._payload_deletes:
            return None
        if key in self._payload_loader_updates:
            return self._payload_loader_updates[key]
        if key[0] in self._payload_hidden_sections:
            return None
        parent = self._parent()
        return parent.lookup_payload_loader(key) if parent is not None else _TRANSACTION_MISSING

    def register_payload_loader(
        self, key: tuple[str, str], loader: Callable[[], Any]
    ) -> None:
        self._payload_deletes.discard(key)
        self._payload_loader_deletes.discard(key)
        self._payload_loader_updates[key] = loader

    def clear_payload_loader(self, key: tuple[str, str]) -> None:
        self._payload_loader_updates.pop(key, None)
        self._payload_loader_deletes.add(key)

    def clear_section_payloads(self, section: str) -> None:
        self._payload_hidden_sections.add(section)
        for key in list(self._payload_updates):
            if key[0] == section:
                self._payload_updates.pop(key, None)
        self._payload_deletes.update(
            key for key in self._store_cls._memory_payloads if key[0] == section
        )
        self._payload_loader_deletes.update(
            key for key in self._store_cls._payload_loaders if key[0] == section
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
            for key in self._payload_loader_deletes:
                parent.clear_payload_loader(key)
            for key, loader in self._payload_loader_updates.items():
                parent.register_payload_loader(key, loader)
        else:
            cls._memory_data.update(self._data_updates)
            if self._payload_hidden_sections:
                for key in list(cls._memory_payloads):
                    if key[0] in self._payload_hidden_sections:
                        cls._memory_payloads.pop(key, None)
            for key in self._payload_deletes:
                cls._memory_payloads.pop(key, None)
                cls._payload_tombstones.add(key)
            cls._memory_payloads.update(self._payload_updates)
            for key in self._payload_updates:
                cls._payload_tombstones.discard(key)
            for key in self._payload_loader_deletes:
                cls._payload_loaders.pop(key, None)
            cls._payload_loaders.update(self._payload_loader_updates)
            for key in self._payload_loader_updates:
                cls._payload_tombstones.discard(key)
        self._finish()

    @property
    def finished(self) -> bool:
        return self._finished


__all__ = ["MiniDatabaseData", "MiniDatabaseStore"]
