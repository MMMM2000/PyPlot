"""Strict JSON-safe codec for Microwire Data Builder projects and stores.

Ordinary decode paths in this module never invoke pickle or import a class named
by untrusted data. Legacy pickle support is deliberately isolated in the
separate, explicitly named ``legacy_migration`` module.
"""

from __future__ import annotations

import base64
import importlib
import inspect
import json
import math
import os
import tempfile
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


CODEC_ENCODING = "microwire-json"
CODEC_VERSION = 2
MAX_CODEC_DEPTH = 100
MAX_CONTAINER_ITEMS = 2_000_000
MAX_NDARRAY_ITEMS = 20_000_000
MAX_JSON_BYTES = 256 * 1024 * 1024
MAX_BINARY_BYTES = 256 * 1024 * 1024
MAX_STRING_CHARS = 64 * 1024 * 1024
MAX_NDARRAY_RANK = 32
MAX_DECODE_NODES = 5_000_000
MAX_DECODE_ITEMS = 4_000_000
MAX_DECODE_BYTES = 512 * 1024 * 1024


class SafeCodecError(ValueError):
    """Raised when data is unsupported, malformed, or outside codec limits."""


class _DecodeBudget:
    def __init__(self) -> None:
        self.nodes = 0
        self.items = 0
        self.bytes = 0

    def consume(self, *, nodes: int = 0, items: int = 0, bytes_: int = 0) -> None:
        self.nodes += nodes
        self.items += items
        self.bytes += bytes_
        if self.nodes > MAX_DECODE_NODES:
            raise SafeCodecError("Aggregate codec node budget exceeded")
        if self.items > MAX_DECODE_ITEMS:
            raise SafeCodecError("Aggregate codec item budget exceeded")
        if self.bytes > MAX_DECODE_BYTES:
            raise SafeCodecError("Aggregate codec byte budget exceeded")


_ALLOWED_TYPES: dict[str, tuple[str, str]] = {
    # Builder records persisted by MiniDatabaseStore/project payloads.
    **{
        f"microwire_data_builder.core:{name}": ("microwire_data_builder.core", name)
        for name in (
            "BuildStats",
            "DmaIsoStressRecord",
            "FabricationIndex",
            "FmrRecord",
            "MeasurementMetadata",
            "MeasurementRecord",
            "MicroscopeCacheEntry",
            "MicroscopeDetection",
            "MicroscopeMeasurements",
            "MicroscopeOCRResult",
            "MiniDmaRecord",
            "ShapeMemoryStressStrainRecord",
            "StrainRecord",
            "VideoMetricsSummary",
            "VsmHysteresisRecord",
            "VsmTemperatureScanRecord",
        )
    },
    **{
        f"plotting.plugins.mini_dma.core:{name}": (
            "plotting.plugins.mini_dma.core",
            name,
        )
        for name in (
            "CurrentSweepBreakSummary",
            "CurrentSweepSummary",
            "CurrentSweepTargetSummary",
            "MiniDmaRun",
        )
    },
    **{
        f"plotting.plugins.vsm_temperature_scan.core:{name}": (
            "plotting.plugins.vsm_temperature_scan.core",
            name,
        )
        for name in ("PlotSeries", "PreparedSeries", "VSMEntry")
    },
    "plotting.plugins.shape_memory_stress_strain.core:DirectionSegment": (
        "plotting.plugins.shape_memory_stress_strain.core",
        "DirectionSegment",
    ),
}


def _check_depth(depth: int) -> None:
    if depth > MAX_CODEC_DEPTH:
        raise SafeCodecError(f"Codec nesting exceeds {MAX_CODEC_DEPTH}")


def _check_size(size: int, label: str, limit: int = MAX_CONTAINER_ITEMS) -> None:
    if size > limit:
        raise SafeCodecError(f"{label} contains {size} items; limit is {limit}")


def _type_id(value: object) -> str:
    module = type(value).__module__
    name = type(value).__qualname__
    # The repository's core tests deliberately load core.py under this alias.
    # Encode those trusted in-process objects using the canonical production ID;
    # decode never accepts or imports the alias.
    if module == "microwire_data_builder_core":
        canonical = f"microwire_data_builder.core:{name}"
        if canonical in _ALLOWED_TYPES:
            canonical_module, canonical_name = _ALLOWED_TYPES[canonical]
            canonical_cls = getattr(importlib.import_module(canonical_module), canonical_name)
            try:
                alias_source = Path(inspect.getsourcefile(type(value)) or "").resolve()
                canonical_source = Path(inspect.getsourcefile(canonical_cls) or "").resolve()
            except (OSError, TypeError):
                pass
            else:
                if alias_source == canonical_source:
                    return canonical
    return f"{module}:{name}"


def encode_value(value: Any, *, _depth: int = 0) -> Any:
    """Encode a supported value into a JSON-compatible tagged tree."""

    _check_depth(_depth)
    if isinstance(value, str) and len(value) > MAX_STRING_CHARS:
        raise SafeCodecError(f"String exceeds {MAX_STRING_CHARS} characters")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"$type": "float", "value": "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")}
    if isinstance(value, bytes):
        _check_size(len(value), "bytes", MAX_BINARY_BYTES)
        return {"$type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Path):
        return {"$type": "path", "value": str(value)}
    if value is pd.NA:
        return {"$type": "pd.NA"}
    if value is pd.NaT:
        return {"$type": "pd.NaT"}
    if isinstance(value, pd.Timestamp):
        return {"$type": "timestamp", "value": value.isoformat()}
    if isinstance(value, datetime):
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"$type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"$type": "time", "value": value.isoformat()}
    if isinstance(value, np.generic):
        dtype = str(value.dtype)
        if dtype == "object":
            raise SafeCodecError("Object numpy scalars are not supported")
        return {
            "$type": "numpy-scalar",
            "dtype": dtype,
            "value": encode_value(value.item(), _depth=_depth + 1),
        }
    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            raise SafeCodecError("Object numpy arrays are not supported")
        _check_size(int(value.size), "numpy array", MAX_NDARRAY_ITEMS)
        return {
            "$type": "ndarray",
            "dtype": str(value.dtype),
            "shape": list(value.shape),
            "data": base64.b64encode(value.tobytes(order="C")).decode("ascii"),
        }
    if isinstance(value, pd.MultiIndex):
        items = list(value.tolist())
        _check_size(len(items), "pandas MultiIndex")
        return {
            "$type": "multi-index",
            "values": encode_value(items, _depth=_depth + 1),
            "names": encode_value(list(value.names), _depth=_depth + 1),
        }
    if isinstance(value, pd.Index):
        items = list(value.tolist())
        _check_size(len(items), "pandas Index")
        return {
            "$type": "index",
            "values": encode_value(items, _depth=_depth + 1),
            "name": encode_value(value.name, _depth=_depth + 1),
            "dtype": str(value.dtype),
        }
    if isinstance(value, pd.Series):
        _check_size(len(value.index), "pandas Series")
        return {
            "$type": "series",
            "index": encode_value(value.index, _depth=_depth + 1),
            "name": encode_value(value.name, _depth=_depth + 1),
            "dtype": str(value.dtype),
            "values": encode_value(value.tolist(), _depth=_depth + 1),
        }
    if isinstance(value, pd.DataFrame):
        _check_size(int(value.shape[0] * max(1, value.shape[1])), "pandas DataFrame")
        return {
            "$type": "dataframe",
            "index": encode_value(value.index, _depth=_depth + 1),
            "columns": encode_value(value.columns, _depth=_depth + 1),
            "dtypes": [str(dtype) for dtype in value.dtypes],
            "rows": encode_value(value.to_numpy(dtype=object).tolist(), _depth=_depth + 1),
        }
    if isinstance(value, Mapping):
        _check_size(len(value), "mapping")
        return {
            "$type": "dict",
            "items": [
                [
                    encode_value(key, _depth=_depth + 1),
                    encode_value(item, _depth=_depth + 1),
                ]
                for key, item in value.items()
            ],
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        _check_size(len(value), type(value).__name__)
        tag = {
            list: "list",
            tuple: "tuple",
            set: "set",
            frozenset: "frozenset",
        }[type(value)]
        return {
            "$type": tag,
            "items": [encode_value(item, _depth=_depth + 1) for item in value],
        }

    type_id = _type_id(value)
    if type_id not in _ALLOWED_TYPES:
        raise SafeCodecError(f"Unsupported type: {type_id}")
    if is_dataclass(value):
        state = {field.name: getattr(value, field.name) for field in fields(value)}
        tag = "dataclass"
    elif type_id == "microwire_data_builder.core:FabricationIndex":
        state = {
            "draw_level": getattr(value, "draw_level", {}),
            "piece_level": getattr(value, "piece_level", {}),
        }
        tag = "object"
    else:
        raise SafeCodecError(f"Allowlisted type has no codec adapter: {type_id}")
    return {
        "$type": tag,
        "class": type_id,
        "state": encode_value(state, _depth=_depth + 1),
    }


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SafeCodecError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SafeCodecError(f"{label} must be a list")
    _check_size(len(value), label)
    return value


def _require_exact_fields(node: Mapping[str, Any], *fields: str) -> None:
    expected = {"$type", *fields}
    if set(node) != expected:
        missing = sorted(expected - set(node))
        extra = sorted(set(node) - expected)
        raise SafeCodecError(
            f"Malformed {node.get('$type')!r} node; missing={missing}, extra={extra}"
        )


def _safe_numpy_dtype(raw: Any) -> np.dtype[Any]:
    try:
        dtype = np.dtype(str(raw))
    except Exception as exc:
        raise SafeCodecError(f"Unsupported numpy dtype: {raw!r}") from exc
    if dtype.hasobject or dtype.kind in {"O", "V"}:
        raise SafeCodecError(f"Unsafe numpy dtype: {dtype}")
    return dtype


def _restore_series_dtype(series: pd.Series, dtype: object) -> pd.Series:
    text = str(dtype or "")
    if not text or text == "object":
        return series
    try:
        return series.astype(text)
    except Exception as exc:
        raise SafeCodecError(f"Invalid pandas dtype: {text!r}") from exc


def decode_value(
    value: Any,
    *,
    _depth: int = 0,
    _budget: _DecodeBudget | None = None,
) -> Any:
    """Decode a strict tagged tree without executing payload-controlled code."""

    _check_depth(_depth)
    if _budget is None:
        _budget = _DecodeBudget()
    _budget.consume(nodes=1)
    if isinstance(value, str) and len(value) > MAX_STRING_CHARS:
        raise SafeCodecError(f"String exceeds {MAX_STRING_CHARS} characters")
    if isinstance(value, str):
        _budget.consume(bytes_=len(value) * 4)
    if isinstance(value, float) and not math.isfinite(value):
        raise SafeCodecError("Non-finite floats must use the tagged float representation")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    node = _require_mapping(value, "codec node")
    tag = node.get("$type")
    if not isinstance(tag, str):
        raise SafeCodecError("Tagged codec object is missing $type")
    schemas = {
        "float": ("value",), "bytes": ("value",), "path": ("value",),
        "timestamp": ("value",), "datetime": ("value",), "date": ("value",),
        "time": ("value",), "pd.NA": (), "pd.NaT": (),
        "numpy-scalar": ("dtype", "value"),
        "ndarray": ("dtype", "shape", "data"),
        "list": ("items",), "tuple": ("items",), "set": ("items",),
        "frozenset": ("items",), "dict": ("items",),
        "index": ("values", "name", "dtype"),
        "multi-index": ("values", "names"),
        "series": ("index", "name", "dtype", "values"),
        "dataframe": ("index", "columns", "dtypes", "rows"),
        "dataclass": ("class", "state"), "object": ("class", "state"),
    }
    if tag not in schemas:
        raise SafeCodecError(f"Unknown codec tag: {tag!r}")
    _require_exact_fields(node, *schemas[tag])
    if tag == "float":
        raw_float = str(node.get("value"))
        if raw_float not in {"nan", "inf", "-inf"}:
            raise SafeCodecError("Invalid tagged float value")
        return {"nan": math.nan, "inf": math.inf, "-inf": -math.inf}[raw_float]
    if tag == "bytes":
        raw = node.get("value")
        if not isinstance(raw, str):
            raise SafeCodecError("bytes value must be text")
        if len(raw) > ((MAX_BINARY_BYTES + 2) // 3) * 4:
            raise SafeCodecError("Encoded bytes payload exceeds limit")
        try:
            decoded_bytes = base64.b64decode(raw.encode("ascii"), validate=True)
        except Exception as exc:
            raise SafeCodecError("Invalid bytes payload") from exc
        if len(decoded_bytes) > MAX_BINARY_BYTES:
            raise SafeCodecError("Decoded bytes payload exceeds limit")
        _budget.consume(bytes_=len(decoded_bytes))
        return decoded_bytes
    if tag == "path":
        return Path(str(node.get("value") or ""))
    if tag in {"timestamp", "datetime", "date", "time"}:
        raw = str(node.get("value") or "")
        try:
            return {
                "timestamp": pd.Timestamp,
                "datetime": datetime.fromisoformat,
                "date": date.fromisoformat,
                "time": time.fromisoformat,
            }[tag](raw)
        except Exception as exc:
            raise SafeCodecError(f"Invalid {tag} value") from exc
    if tag == "pd.NA":
        return pd.NA
    if tag == "pd.NaT":
        return pd.NaT
    if tag == "numpy-scalar":
        dtype = _safe_numpy_dtype(node.get("dtype"))
        item = decode_value(node.get("value"), _depth=_depth + 1, _budget=_budget)
        try:
            return dtype.type(item)
        except Exception as exc:
            raise SafeCodecError("Invalid numpy scalar") from exc
    if tag == "ndarray":
        dtype = _safe_numpy_dtype(node.get("dtype"))
        shape = _require_list(node.get("shape"), "ndarray shape")
        if len(shape) > MAX_NDARRAY_RANK:
            raise SafeCodecError(f"ndarray rank exceeds {MAX_NDARRAY_RANK}")
        if not all(isinstance(size, int) and size >= 0 for size in shape):
            raise SafeCodecError("Invalid ndarray shape")
        item_count = math.prod(shape)
        _check_size(item_count, "numpy array", MAX_NDARRAY_ITEMS)
        raw = node.get("data")
        if not isinstance(raw, str):
            raise SafeCodecError("ndarray data must be text")
        expected = item_count * dtype.itemsize
        _check_size(expected, "numpy array bytes", MAX_BINARY_BYTES)
        max_encoded = ((expected + 2) // 3) * 4
        if len(raw) > max_encoded:
            raise SafeCodecError("Encoded ndarray data exceeds declared shape/dtype")
        try:
            binary = base64.b64decode(raw.encode("ascii"), validate=True)
        except Exception as exc:
            raise SafeCodecError("Invalid ndarray data") from exc
        if len(binary) != expected:
            raise SafeCodecError("ndarray byte length does not match shape/dtype")
        _budget.consume(items=item_count, bytes_=len(binary))
        return np.frombuffer(binary, dtype=dtype).copy().reshape(tuple(shape))
    if tag in {"list", "tuple", "set", "frozenset"}:
        raw_items = _require_list(node.get("items"), tag)
        _budget.consume(items=len(raw_items))
        items = [
            decode_value(item, _depth=_depth + 1, _budget=_budget)
            for item in raw_items
        ]
        try:
            return {"list": list, "tuple": tuple, "set": set, "frozenset": frozenset}[tag](items)
        except TypeError as exc:
            raise SafeCodecError(f"Decoded {tag} contains an unhashable item") from exc
    if tag == "dict":
        result: dict[Any, Any] = {}
        raw_pairs = _require_list(node.get("items"), "dict items")
        _budget.consume(items=len(raw_pairs))
        for pair in raw_pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                raise SafeCodecError("dict item must be a key/value pair")
            key = decode_value(pair[0], _depth=_depth + 1, _budget=_budget)
            try:
                result[key] = decode_value(
                    pair[1], _depth=_depth + 1, _budget=_budget
                )
            except TypeError as exc:
                raise SafeCodecError("Decoded mapping key is not hashable") from exc
        return result
    if tag in {"index", "multi-index"}:
        values = decode_value(node.get("values"), _depth=_depth + 1, _budget=_budget)
        if not isinstance(values, list):
            raise SafeCodecError("Index values must decode to a list")
        if tag == "multi-index":
            names = decode_value(node.get("names"), _depth=_depth + 1, _budget=_budget)
            return pd.MultiIndex.from_tuples([tuple(item) for item in values], names=names)
        name = decode_value(node.get("name"), _depth=_depth + 1, _budget=_budget)
        try:
            return pd.Index(values, name=name, dtype=str(node.get("dtype") or None))
        except Exception as exc:
            raise SafeCodecError(f"Invalid pandas Index dtype: {node.get('dtype')!r}") from exc
    if tag == "series":
        index = decode_value(node.get("index"), _depth=_depth + 1, _budget=_budget)
        values = decode_value(node.get("values"), _depth=_depth + 1, _budget=_budget)
        name = decode_value(node.get("name"), _depth=_depth + 1, _budget=_budget)
        if not isinstance(index, pd.Index) or not isinstance(values, list):
            raise SafeCodecError("Malformed pandas Series")
        return _restore_series_dtype(pd.Series(values, index=index, name=name), node.get("dtype"))
    if tag == "dataframe":
        index = decode_value(node.get("index"), _depth=_depth + 1, _budget=_budget)
        columns = decode_value(node.get("columns"), _depth=_depth + 1, _budget=_budget)
        rows = decode_value(node.get("rows"), _depth=_depth + 1, _budget=_budget)
        dtypes = _require_list(node.get("dtypes"), "DataFrame dtypes")
        if not isinstance(index, pd.Index) or not isinstance(columns, pd.Index) or not isinstance(rows, list):
            raise SafeCodecError("Malformed pandas DataFrame")
        if len(dtypes) != len(columns):
            raise SafeCodecError("DataFrame dtype count does not match columns")
        if len(index) != len(rows):
            raise SafeCodecError("DataFrame row count does not match index")
        _check_size(len(index) * max(1, len(columns)), "pandas DataFrame")
        _budget.consume(items=len(index) * max(1, len(columns)))
        if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
            raise SafeCodecError("DataFrame row width does not match columns")
        frame = pd.DataFrame(rows, index=index, columns=columns)
        for position, dtype in enumerate(dtypes):
            restored = _restore_series_dtype(frame.iloc[:, position], dtype)
            frame.isetitem(position, restored)
        return frame
    if tag in {"dataclass", "object"}:
        type_id = node.get("class")
        if not isinstance(type_id, str) or type_id not in _ALLOWED_TYPES:
            raise SafeCodecError(f"Class is not allowlisted: {type_id!r}")
        fabrication_type = "microwire_data_builder.core:FabricationIndex"
        if tag == "object" and type_id != fabrication_type:
            raise SafeCodecError(f"No object adapter for {type_id}")
        if tag == "dataclass" and type_id == fabrication_type:
            raise SafeCodecError(f"No dataclass adapter for {type_id}")
        state = decode_value(node.get("state"), _depth=_depth + 1, _budget=_budget)
        if not isinstance(state, dict) or not all(isinstance(key, str) for key in state):
            raise SafeCodecError("Object state must be a string-keyed mapping")
        module_name, class_name = _ALLOWED_TYPES[type_id]
        cls = getattr(importlib.import_module(module_name), class_name)
        if tag == "dataclass":
            allowed_fields = {field.name for field in fields(cls)}
            if not set(state).issubset(allowed_fields):
                raise SafeCodecError(f"Unexpected fields for {type_id}")
            try:
                return cls(**state)
            except Exception as exc:
                raise SafeCodecError(f"Invalid state for {type_id}") from exc
        instance = cls()
        if type_id == "microwire_data_builder.core:FabricationIndex":
            if set(state) != {"draw_level", "piece_level"} or not all(
                isinstance(state[key], dict) for key in ("draw_level", "piece_level")
            ):
                raise SafeCodecError("FabricationIndex state is malformed")
            instance.draw_level = state["draw_level"]
            instance.piece_level = state["piece_level"]
            return instance
        raise SafeCodecError(f"No object adapter for {type_id}")
    raise SafeCodecError(f"Unknown codec tag: {tag!r}")


def encode_envelope(value: Any) -> dict[str, Any]:
    return {
        "encoding": CODEC_ENCODING,
        "version": CODEC_VERSION,
        "value": encode_value(value),
    }


def decode_envelope(payload: Any) -> Any:
    node = _require_mapping(payload, "codec envelope")
    if set(node) != {"encoding", "version", "value"}:
        raise SafeCodecError("Builder codec envelope has missing or unexpected fields")
    if node.get("encoding") != CODEC_ENCODING or node.get("version") != CODEC_VERSION:
        raise SafeCodecError("Unsupported Builder codec encoding/version")
    return decode_value(node["value"])


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON next to its destination and atomically replace on success."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        if temp_path.stat().st_size > MAX_JSON_BYTES:
            raise SafeCodecError(
                f"Encoded JSON exceeds the safe file limit of {MAX_JSON_BYTES} bytes"
            )
        os.replace(temp_path, target)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def read_json_file(path: Path) -> Any:
    """Read a size-bounded JSON document."""

    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise SafeCodecError(f"Cannot stat JSON file: {target}") from exc
    if size > MAX_JSON_BYTES:
        raise SafeCodecError(f"JSON file exceeds {MAX_JSON_BYTES} bytes: {target}")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SafeCodecError(f"Invalid JSON file: {target}") from exc


__all__ = [
    "CODEC_ENCODING",
    "CODEC_VERSION",
    "MAX_JSON_BYTES",
    "SafeCodecError",
    "atomic_write_json",
    "decode_envelope",
    "decode_value",
    "encode_envelope",
    "encode_value",
    "read_json_file",
]
