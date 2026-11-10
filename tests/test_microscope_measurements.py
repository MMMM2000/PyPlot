from __future__ import annotations

import csv
from dataclasses import asdict
import hashlib
import json
import math
import struct
import zlib

import pytest

from data_logging.microscope_logger.measurements import (
    Calibration, make_calibration, measure_line, save_session,
)


CONTEXT = {"objective": "4x", "image_size": (100, 80), "camera_id": "test-camera", "zoom": 1.0}


def png_bytes(width=100, height=80):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    pixels = b"".join(b"\0" + bytes([80, 120, 150]) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


@pytest.mark.parametrize("start,end,expected", [((0, 0), (3, 4), 5), ((20, 10), (20, 30), 20),
                                                ((5, 10), (65, 10), 60)])
def test_native_line_geometry_is_independent_of_rotation(start, end, expected):
    record = measure_line(start, end, **CONTEXT)
    assert record["width_px"] == pytest.approx(expected)
    assert record["width_um"] is None
    assert record["calibration_status"] == "Uncalibrated"


def test_known_reference_converts_width_and_keeps_provenance():
    scale = make_calibration(50, 10, source="Synthetic 10 um reference", **CONTEXT)
    record = measure_line((10, 10), (40, 50), calibration=scale, kind="outer", **CONTEXT)
    assert record["width_px"] == 50
    assert record["width_um"] == 10
    assert record["calibration"]["source"] == "Synthetic 10 um reference"
    assert record["kind"] == "outer"
    assert scale.um_per_pixel == 0.2


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["pixel_distance", "known_um"])
def test_invalid_reference_is_rejected(value, field):
    values = {"pixel_distance": 50, "known_um": 10, field: value}
    with pytest.raises(ValueError):
        make_calibration(**values, **CONTEXT)


@pytest.mark.parametrize("changed", [{"objective": "10x"}, {"image_size": (200, 160)},
                                      {"camera_id": "other-camera"}, {"zoom": 2.0}])
def test_calibration_cannot_be_reused_for_another_acquisition(changed):
    scale = make_calibration(50, 10, **CONTEXT)
    with pytest.raises(ValueError, match="does not match"):
        measure_line((10, 10), (30, 10), calibration=scale, **(CONTEXT | changed))


@pytest.mark.parametrize("start,end", [((0, 0), (0, 0)), ((-1, 0), (2, 0)),
                                       ((1, 1), (100, 79)), ((1, 1), (2, math.nan))])
def test_invalid_or_outside_line_rejected(start, end):
    with pytest.raises(ValueError):
        measure_line(start, end, **CONTEXT)


def test_calibration_snapshot_roundtrip_and_objective_normalization():
    scale = make_calibration(50, 10, **(CONTEXT | {"objective": "4×"}))
    restored = Calibration(**json.loads(json.dumps(asdict(scale))))
    assert restored == scale
    assert restored.matches(**CONTEXT)
    with pytest.raises(ValueError, match="reference length"):
        Calibration(**(asdict(scale) | {"um_per_pixel": 2}))


@pytest.mark.parametrize("dimensions", [(0, 10), (10, -1), (True, 10), (10.5, 10),
                                        (math.inf, 10), (math.nan, 10), (10,)])
def test_invalid_image_dimensions_are_rejected(dimensions):
    with pytest.raises(ValueError, match="image_size"):
        measure_line((0, 0), (1, 1), **(CONTEXT | {"image_size": dimensions}))


def test_physical_width_overflow_is_rejected():
    scale = make_calibration(1, 1e308, **CONTEXT)
    with pytest.raises(ValueError, match="finite numeric range"):
        measure_line((0, 0), (3, 4), calibration=scale, **CONTEXT)


def test_export_preserves_original_and_each_records_sample_and_scale(tmp_path):
    source = tmp_path / "source.png"
    original = png_bytes()
    source.write_bytes(original)
    first = measure_line((0, 0), (3, 4), **CONTEXT)
    first["sample"] = {"composition": "Synthetic-A", "microwire": "001",
                       "source_kind": "project", "source_path": "synthetic.pydpj", "source_row": 2,
                       "project_sha256": "test-source-sha"}
    second = measure_line((10, 20), (20, 20), calibration=make_calibration(50, 10, **CONTEXT),
                          kind="core", **CONTEXT)
    second["sample"] = {"composition": "Synthetic-B", "microwire": "002", "source_kind": "fabrication"}
    second["review_note"] = "Extra provenance stays attached"
    metadata = dict(CONTEXT, sample={"composition": "Current-ui-selection", "microwire": "003"})
    folder = save_session(tmp_path, original, [first, second], metadata)
    data = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert (folder / "image.png").read_bytes() == source.read_bytes() == original
    assert data["image"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert data["measurements"][0]["width_um"] is None
    assert data["measurements"][0]["sample"] == first["sample"]
    assert data["measurements"][1]["width_um"] == 2
    assert data["measurements"][1]["review_note"] == second["review_note"]
    with (folder / "measurements.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [(r["composition"], r["microwire"]) for r in rows] == [("Synthetic-A", "001"), ("Synthetic-B", "002")]
    assert rows[0]["width_um"] == ""
    assert rows[1]["kind"] == "core"
    again = save_session(tmp_path, original, [first, second], metadata)
    assert again != folder
    assert (folder / "image.png").read_bytes() == original


@pytest.mark.parametrize("defect", ["bad-png", "wrong-size", "nan-metadata", "forged-width", "unscaled-um"])
def test_invalid_export_does_not_create_a_session(tmp_path, defect):
    target = tmp_path / "exports"
    original, metadata = png_bytes(), dict(CONTEXT)
    record = measure_line((0, 0), (3, 4), **CONTEXT)
    if defect == "bad-png":
        original = original[:-10]
    elif defect == "wrong-size":
        metadata["image_size"] = (999, 999)
    elif defect == "nan-metadata":
        metadata["unknown"] = math.nan
    elif defect == "forged-width":
        record["width_px"] = 42
    elif defect == "unscaled-um":
        record["width_um"] = 1.2
    with pytest.raises(ValueError):
        save_session(target, original, [record], metadata)
    assert not target.exists()


def test_failed_write_keeps_only_a_recoverable_pending_folder(tmp_path, monkeypatch):
    original_open = type(tmp_path).open

    def fail_json(path, *args, **kwargs):
        if path.name == "session.json":
            raise OSError("Synthetic disk write failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "open", fail_json)
    with pytest.raises(OSError, match="Synthetic disk"):
        save_session(tmp_path, png_bytes(), [], CONTEXT)
    folders = list(tmp_path.iterdir())
    assert len(folders) == 1
    assert folders[0].name.startswith(".microscope_pending_")
    assert (folders[0] / "image.png").read_bytes() == png_bytes()


def test_export_includes_original_reference_png_and_portable_binding(tmp_path):
    image = png_bytes()
    reference = png_bytes(200, 150)
    reference_sha = hashlib.sha256(reference).hexdigest()
    reference_file = f"reference_{reference_sha}.png"
    record = measure_line((10, 20), (20, 20), calibration=make_calibration(50, 10, **CONTEXT),
                          **CONTEXT)
    record["calibration_evidence"] = {
        "image_sha256": reference_sha, "image_file": reference_file,
        "start": [20, 20], "end": [70, 20], "source": "Synthetic calibration reference",
    }
    metadata = dict(CONTEXT, calibration_evidence=record["calibration_evidence"])
    references = {reference_sha: reference}
    folder = save_session(tmp_path, image, [record], metadata, reference_images=references)
    assert (folder / reference_file).read_bytes() == reference
    assert references == {reference_sha: reference}  # Caller inputs remain intact.
    assert (folder / "image.png").read_bytes() == image
    payload = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    assert payload["reference_images"] == [{
        "file": reference_file, "sha256": reference_sha, "width": 200, "height": 150}]
    assert payload["measurements"][0]["calibration_evidence"] == record["calibration_evidence"]
    assert payload["metadata"]["calibration_evidence"] == metadata["calibration_evidence"]


@pytest.mark.parametrize("defect", ["wrong-sha", "uppercase-sha", "short-sha", "not-png", "corrupt-png"])
def test_invalid_reference_does_not_create_session(tmp_path, defect):
    target = tmp_path / "exports"
    reference = png_bytes(200, 150)
    reference_sha = hashlib.sha256(reference).hexdigest()
    if defect == "wrong-sha":
        reference_sha = "0" * 64
    elif defect == "uppercase-sha":
        reference_sha = reference_sha.upper()
    elif defect == "short-sha":
        reference_sha = "a" * 10
    elif defect == "not-png":
        reference = b"This is not an image"
        reference_sha = hashlib.sha256(reference).hexdigest()
    elif defect == "corrupt-png":
        reference = reference[:-5]
        reference_sha = hashlib.sha256(reference).hexdigest()
    with pytest.raises(ValueError):
        save_session(target, png_bytes(), [], CONTEXT,
                     reference_images={reference_sha: reference})
    assert not target.exists()


def test_failed_reference_write_is_not_published_as_a_completed_session(tmp_path, monkeypatch):
    original_open = type(tmp_path).open
    reference = png_bytes(200, 150)
    reference_sha = hashlib.sha256(reference).hexdigest()

    def fail_reference(path, *args, **kwargs):
        if path.name.startswith("reference_"):
            raise OSError("Synthetic reference write failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "open", fail_reference)
    with pytest.raises(OSError, match="Synthetic reference"):
        save_session(tmp_path, png_bytes(), [], CONTEXT,
                     reference_images={reference_sha: reference})
    folders = list(tmp_path.iterdir())
    assert len(folders) == 1
    assert folders[0].name.startswith(".microscope_pending_")
    assert (folders[0] / "image.png").read_bytes() == png_bytes()


def test_transient_windows_publish_lock_keeps_the_complete_original(tmp_path, monkeypatch):
    from data_logging.microscope_logger import measurements
    original_rename = type(tmp_path).rename
    calls = []
    def locked_once(path, target):
        calls.append(path)
        if len(calls) == 1:
            error = PermissionError("Synthetic Windows sharing lock")
            error.winerror = 5
            raise error
        return original_rename(path, target)
    monkeypatch.setattr(type(tmp_path), "rename", locked_once)
    monkeypatch.setattr(measurements.time, "sleep", lambda delay: None)
    session = save_session(tmp_path, png_bytes(), [], CONTEXT)
    assert len(calls) == 2
    assert (session / "image.png").read_bytes() == png_bytes()


def test_publish_never_replaces_an_existing_destination(tmp_path):
    from data_logging.microscope_logger.measurements import _publish_directory
    pending, destination = tmp_path / "pending", tmp_path / "existing"
    pending.mkdir()
    destination.mkdir()
    (destination / "preserve.txt").write_text("user work")
    with pytest.raises(FileExistsError):
        _publish_directory(pending, destination)
    assert pending.exists()
    assert (destination / "preserve.txt").read_text() == "user work"
