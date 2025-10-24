from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from PyQt6 import QtCore


def _storage_root() -> Path:
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


@dataclass
class MiniDatabaseData:
    """Persisted state for a single mini database section."""

    sources: List[str] = field(default_factory=list)
    processed: Dict[str, float] = field(default_factory=dict)
    table: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())
    extra: Dict[str, Any] = field(default_factory=dict)


class MiniDatabaseStore:
    """Load and save ``MiniDatabaseData`` records for individual sections."""

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

    def load(self) -> MiniDatabaseData:
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
        return data

    def save(self, data: MiniDatabaseData) -> None:
        meta = {
            "sources": list(dict.fromkeys(data.sources)),
            "processed": data.processed,
            "extra": data.extra,
        }
        self._meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        data.table.to_pickle(self._table_path)

    def clear_table(self) -> None:
        try:
            self._table_path.unlink()
        except FileNotFoundError:
            pass

    def payload_path(self, name: str) -> Path:
        safe = name.replace(os.sep, "_").replace("..", "_")
        return self._payload_dir / f"{self.section}_{safe}.pkl"

    def save_payload(self, name: str, payload: Any) -> Path:
        path = self.payload_path(name)
        pd.to_pickle(payload, path)
        return path

    def load_payload(self, name: str) -> Any:
        path = self.payload_path(name)
        if path.exists():
            try:
                return pd.read_pickle(path)
            except Exception:
                return None
        return None

    def clear_payload(self, name: str) -> None:
        path = self.payload_path(name)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


__all__ = ["MiniDatabaseData", "MiniDatabaseStore"]
