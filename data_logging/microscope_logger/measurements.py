"""Native-pixel line measurements and provenance-preserving session exports.

This module does not access cameras, application settings or Builder projects.
An absent calibration always leaves physical widths unknown.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import time
from typing import Any, Mapping, Sequence
import uuid
import zlib


def _positive(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return result


def _size(value: Sequence[int]) -> tuple[int, int]:
    try:
        invalid = len(value) != 2 or any(
            isinstance(v, bool) or int(v) != v or v <= 0 for v in value)
    except (TypeError, ValueError, OverflowError):
        invalid = True
    if invalid:
        raise ValueError("image_size must contain two positive integer dimensions")
    return int(value[0]), int(value[1])


def _objective(value: str) -> str:
    result = str(value).strip().lower().replace("×", "x")
    if not result:
        raise ValueError("An objective label is required")
    return result


@dataclass(frozen=True)
class Calibration:
    """A reference scale bound to one explicitly identified acquisition setup."""

    um_per_pixel: float
    reference_pixels: float
    reference_length_um: float
    objective: str
    image_size: tuple[int, int]
    camera_id: str = ""
    zoom: float = 1.0
    source: str = "Known reference length"

    def __post_init__(self) -> None:
        scale = _positive(self.um_per_pixel, "um_per_pixel")
        pixels = _positive(self.reference_pixels, "reference_pixels")
        length = _positive(self.reference_length_um, "reference_length_um")
        if not math.isclose(scale, length / pixels, rel_tol=1e-12):
            raise ValueError("Calibration scale does not match its reference length")
        object.__setattr__(self, "um_per_pixel", scale)
        object.__setattr__(self, "reference_pixels", pixels)
        object.__setattr__(self, "reference_length_um", length)
        object.__setattr__(self, "objective", _objective(self.objective))
        object.__setattr__(self, "image_size", _size(self.image_size))
        object.__setattr__(self, "camera_id", str(self.camera_id))
        object.__setattr__(self, "zoom", _positive(self.zoom, "zoom"))
        if not str(self.source).strip():
            raise ValueError("Calibration reference source is required")

    def matches(self, *, objective: str, image_size: Sequence[int],
                camera_id: str = "", zoom: float = 1.0) -> bool:
        return (self.objective == _objective(objective)
                and self.image_size == _size(image_size)
                and self.camera_id == str(camera_id)
                and math.isclose(self.zoom, _positive(zoom, "zoom"), rel_tol=1e-12))


def make_calibration(pixel_distance: float, known_um: float, *, objective: str,
                     image_size: Sequence[int], camera_id: str = "", zoom: float = 1.0,
                     source: str = "Known reference length") -> Calibration:
    """Create a scale from a known length measured in native image pixels."""
    pixels = _positive(pixel_distance, "pixel_distance")
    length = _positive(known_um, "known_um")
    return Calibration(length / pixels, pixels, length, objective, _size(image_size),
                       str(camera_id), zoom, source)


def _point(point: Sequence[float], image_size: tuple[int, int]) -> tuple[float, float]:
    if len(point) != 2:
        raise ValueError("Each endpoint must contain x and y")
    x, y = map(float, point)
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("Line endpoints must be finite")
    if not (0 <= x <= image_size[0] - 1 and 0 <= y <= image_size[1] - 1):
        raise ValueError("Line endpoints must lie inside the native image")
    return x, y


def measure_line(start: Sequence[float], end: Sequence[float], *,
                 image_size: Sequence[int], objective: str, camera_id: str = "",
                 zoom: float = 1.0, calibration: Calibration | None = None,
                 kind: str = "apparent") -> dict[str, Any]:
    """Measure a user-selected line; the caller chooses a perpendicular width.

    The returned record contains its own acquisition and calibration snapshot,
    so a later change of sample or calibration cannot silently relabel it.
    """
    dimensions = _size(image_size)
    objective = _objective(objective)
    zoom = _positive(zoom, "zoom")
    if kind not in {"apparent", "outer", "core"}:
        raise ValueError("kind must be apparent, outer or core")
    first, last = _point(start, dimensions), _point(end, dimensions)
    distance = math.hypot(last[0] - first[0], last[1] - first[1])
    if distance <= 0:
        raise ValueError("Choose two different endpoints")
    if calibration is not None and not calibration.matches(
        objective=objective, image_size=dimensions, camera_id=camera_id, zoom=zoom
    ):
        raise ValueError("Calibration does not match this objective, image size, camera or zoom")
    physical_width = None if calibration is None else distance * calibration.um_per_pixel
    if physical_width is not None and not math.isfinite(physical_width):
        raise ValueError("Calibrated width is outside the finite numeric range")
    return {
        "start": list(first), "end": list(last), "width_px": distance,
        "width_um": physical_width,
        "kind": kind, "calibration_status": "Uncalibrated" if calibration is None else "Calibrated",
        "objective": objective, "image_size": list(dimensions), "camera_id": str(camera_id),
        "zoom": zoom, "calibration": None if calibration is None else asdict(calibration),
    }


def _png_size(data: bytes) -> tuple[int, int]:
    """Check a PNG container and return the unchanged image's IHDR dimensions."""
    if not isinstance(data, bytes) or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Original image must be PNG bytes")
    offset, dimensions, has_pixels = 8, None, False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("Truncated PNG data")
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != expected_crc:
            raise ValueError("PNG checksum failed")
        if offset == 8:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG has no valid image header")
            dimensions = _size(struct.unpack(">II", body[:8]))
        elif kind == b"IHDR":
            raise ValueError("PNG has duplicate image headers")
        has_pixels |= kind == b"IDAT"
        if kind == b"IEND":
            if length or dimensions is None or not has_pixels or end != len(data):
                raise ValueError("PNG has no complete image")
            return dimensions
        offset = end
    raise ValueError("PNG has no complete image")


def _validated_record(record: Mapping[str, Any], image_size: tuple[int, int]) -> dict[str, Any]:
    """Check recorded math without replacing any per-measurement provenance."""
    if _size(record["image_size"]) != image_size:
        raise ValueError("A measurement belongs to a different image size")
    raw_calibration = record.get("calibration")
    calibration = Calibration(**raw_calibration) if raw_calibration is not None else None
    computed = measure_line(record["start"], record["end"], image_size=record["image_size"],
                            objective=record["objective"], camera_id=record.get("camera_id", ""),
                            zoom=record.get("zoom", 1.0), calibration=calibration,
                            kind=record.get("kind", "apparent"))
    for key in ("width_px", "width_um"):
        expected, actual = computed[key], record.get(key)
        if (expected is None and actual is not None) or (expected is not None and (
            actual is None or not math.isfinite(float(actual))
            or not math.isclose(float(actual), expected, rel_tol=1e-10, abs_tol=1e-10)
        )):
            raise ValueError(f"Recorded {key} does not match its line and calibration")
    if record.get("calibration_status") != computed["calibration_status"]:
        raise ValueError("Recorded calibration status does not match its scale")
    return dict(record)


def _publish_directory(pending: Path, destination: Path) -> None:
    """Publish a newly owned directory, tolerating brief Windows sharing locks."""
    for attempt in range(6):
        if destination.exists():
            raise FileExistsError(f"Refusing to replace an existing session: {destination}")
        try:
            pending.rename(destination)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 5:
                raise
            time.sleep(0.1 * (attempt + 1))


def save_session(output_parent: str | Path, image_png_bytes: bytes,
                 records: Sequence[Mapping[str, Any]], metadata: Mapping[str, Any], *,
                 reference_images: Mapping[str, bytes] | None = None) -> Path:
    """Create a new session folder; never rewrite an image or Builder source.

    Metadata may describe the current UI selection. Each measurement's own
    ``sample`` and ``calibration`` fields remain authoritative and are preserved.
    Optional calibration reference PNGs are keyed by their lowercase SHA-256;
    they are verified and copied unchanged into the completed session folder.
    """
    dimensions = _png_size(image_png_bytes)
    if "image_size" in metadata and _size(metadata["image_size"]) != dimensions:
        raise ValueError("Session image_size does not match the original PNG")
    checked = [_validated_record(r, dimensions) for r in records]
    stamp = datetime.now(timezone.utc)
    session_id = uuid.uuid4().hex
    payload = {
        "schema_version": 1, "application": "PyPlot microscope",
        "session_id": session_id, "created_utc": stamp.isoformat(),
        "image": {"file": "image.png", "sha256": hashlib.sha256(image_png_bytes).hexdigest(),
                  "width": dimensions[0], "height": dimensions[1]},
        "metadata": dict(metadata), "measurements": checked,
    }
    checked_references: dict[str, bytes] = {}
    reference_inventory = []
    for expected_sha, reference_data in (reference_images or {}).items():
        if (not isinstance(expected_sha, str) or len(expected_sha) != 64
                or any(char not in "0123456789abcdef" for char in expected_sha)):
            raise ValueError("Reference image keys must be lowercase SHA-256 values")
        reference_size = _png_size(reference_data)
        if hashlib.sha256(reference_data).hexdigest() != expected_sha:
            raise ValueError("Reference image SHA-256 does not match its original bytes")
        checked_references[expected_sha] = reference_data
        reference_inventory.append({
            "file": f"reference_{expected_sha}.png", "sha256": expected_sha,
            "width": reference_size[0], "height": reference_size[1],
        })
    if reference_inventory:
        payload["reference_images"] = reference_inventory
    # Validate JSON before creating anything, including arbitrary extra fields.
    json_text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    fields = ["measurement", "composition", "microwire", "specimen", "sample_uid", "source_kind", "source_path", "brittle", "brittle_observed_utc",
              "kind", "width_px", "width_um", "calibration_status", "review_status", "reviewed_utc", "objective",
              "zoom", "camera_id", "start_x_px", "start_y_px", "end_x_px", "end_y_px"]
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=fields)
    writer.writeheader()
    for index, record in enumerate(checked, 1):
        sample = record.get("sample") or {}
        if not isinstance(sample, Mapping):
            raise ValueError("Measurement sample must be a mapping")
        row = {key: record.get(key) for key in fields if key in record}
        row.update({key: sample.get(key, "") for key in
                    ("composition", "microwire", "specimen", "source_kind", "source_path")})
        identity = sample.get("physical_sample")
        row["sample_uid"] = identity.get("uid", "") if isinstance(identity, Mapping) else ""
        row.update(brittle=sample.get("brittle"), brittle_observed_utc=sample.get("brittle_observed_utc"),
                   measurement=index, start_x_px=record["start"][0], start_y_px=record["start"][1],
                   end_x_px=record["end"][0], end_y_px=record["end"][1])
        writer.writerow(row)
    parent = Path(output_parent)
    parent.mkdir(parents=True, exist_ok=True)
    from .session_naming import new_session_name
    directory = parent / new_session_name(metadata, timestamp=stamp, token=session_id)
    pending = parent / f".microscope_pending_{session_id}"
    pending.mkdir(exist_ok=False)
    with (pending / "image.png").open("xb") as stream:
        stream.write(image_png_bytes)
    for reference_sha, reference_data in checked_references.items():
        with (pending / f"reference_{reference_sha}.png").open("xb") as stream:
            stream.write(reference_data)
    with (pending / "measurements.csv").open("x", encoding="utf-8", newline="") as stream:
        stream.write(csv_buffer.getvalue())
    with (pending / "session.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json_text + "\n")
    # A failed write can leave recoverable pending files, never a completed-looking session.
    _publish_directory(pending, directory)
    return directory
