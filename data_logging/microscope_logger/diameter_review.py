"""Eligibility and descriptive summaries for explicitly reviewed diameters.

These pure helpers do not access images, settings or Builder projects. A reviewed
label records the operator's decision; it does not establish a material boundary.
Publication must separately verify the retained evidence and current project.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
from statistics import mean, stdev

from .measurements import Calibration, _size, _validated_record, measure_line
from .samples import _wire_display


def _sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _same_sample(value: Mapping, composition: str, microwire: str) -> bool:
    return (isinstance(value.get("composition"), str)
            and value["composition"].strip() == composition
            and _wire_display(value.get("microwire")) == microwire)


def require_wire_level_diameters(records: Sequence[Mapping]) -> None:
    """Current Builder writer updates a wire, not an individual physical piece."""
    if any(isinstance(record.get("sample"), Mapping) and record["sample"].get("specimen")
           for record in records):
        raise ValueError("Piece-specific diameter cannot be applied to wire-level project rows; retain it in the capture")


def _has_project_binding(sample: Mapping, composition: str, microwire: str) -> bool:
    alternatives = sample.get("matching_sources", [])
    if not isinstance(alternatives, (list, tuple)):
        return False
    for binding in (sample, *alternatives):
        if (not isinstance(binding, Mapping) or binding.get("source_kind") != "project"
                or not _same_sample(binding, composition, microwire)
                or not isinstance(binding.get("source_path"), str) or not binding["source_path"].strip()
                or not _sha256(binding.get("source_sha256"))):
            continue
        rows = binding.get("source_rows")
        if (isinstance(rows, (list, tuple)) and rows and all(
            isinstance(row, Mapping) and row.get("binding_type") == "project_table_row"
            and isinstance(row.get("section"), str) and bool(row["section"].strip())
            and type(row.get("row_index")) is int and row["row_index"] >= 0
            and _same_sample(row, composition, microwire) for row in rows
        )):
            return True
    return False


def _validate_evidence(record: Mapping, calibration: Calibration) -> None:
    evidence = record.get("calibration_evidence")
    if not isinstance(evidence, Mapping) or not _sha256(evidence.get("image_sha256")):
        raise ValueError("Retained calibration reference evidence with SHA-256 is required")
    distance = measure_line(evidence["start"], evidence["end"],
                            image_size=calibration.image_size, objective=calibration.objective)["width_px"]
    if not math.isclose(distance, calibration.reference_pixels, rel_tol=1e-12):
        raise ValueError("Calibration reference geometry does not match its scale")


def summarize_reviewed_diameters(records: Sequence[Mapping], composition: str, microwire: str,
                                 selected_rows: Sequence[int]) -> dict:
    """Summarize explicitly selected, reviewed core/outer values in micrometres.

    Measurement numbers are one based and sorted in the result. Each selected
    record must have consistent native-pixel/calibration math, retained reference
    geometry/hash, and an original project row binding for the exact specimen.
    Original source hashes may predate the current project; publication checks
    that distinction against an explicit expected current target SHA separately.

    ``std_sample_um`` is descriptive sample standard deviation, or ``None`` for
    a single value. It is not a measurement uncertainty or an accuracy estimate.
    No records are modified, including unselected or historical measurements.
    """
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError("Measurements must be a sequence of records")
    if not isinstance(composition, str) or not composition.strip():
        raise ValueError("Select an exact composition")
    if not isinstance(microwire, str) or not microwire.strip():
        raise ValueError("Select an exact microwire")
    composition, microwire = composition.strip(), _wire_display(microwire)
    if (not isinstance(selected_rows, Sequence) or isinstance(selected_rows, (str, bytes))
            or not selected_rows):
        raise ValueError("Select at least one measurement number")
    if any(type(number) is not int or not 1 <= number <= len(records) for number in selected_rows):
        raise ValueError("Measurement numbers must be valid one-based integers")
    if len(set(selected_rows)) != len(selected_rows):
        raise ValueError("A measurement number cannot be selected twice")
    selected = sorted(selected_rows)
    grouped = {"core": [], "outer": []}
    piece_keys = set()
    for number in selected:
        record = records[number - 1]
        try:
            if not isinstance(record, Mapping):
                raise ValueError("Measurement must be a record")
            if record.get("review_status") != "Reviewed":
                raise ValueError("Explicit Reviewed status is required")
            kind = record.get("kind")
            if kind not in grouped:
                raise ValueError("Select core or outer diameters; apparent widths cannot be published")
            sample = record.get("sample")
            if not isinstance(sample, Mapping) or not _same_sample(sample, composition, microwire):
                raise ValueError("Measurement belongs to a different specimen")
            identity = sample.get("physical_sample")
            uid = identity.get("uid", "") if isinstance(identity, Mapping) else ""
            piece_keys.add((sample.get("specimen", ""), uid))
            if len(piece_keys) > 1:
                raise ValueError("Measurements belong to different or incompletely identified physical pieces")
            if not _has_project_binding(sample, composition, microwire):
                raise ValueError("An exact original project row binding with SHA-256 is required")
            if record.get("calibration_status") != "Calibrated" or not isinstance(record.get("calibration"), Mapping):
                raise ValueError("Calibrated micrometre values are required; pixel-only values cannot be published")
            if any(isinstance(record.get(key), bool) for key in ("width_px", "width_um")):
                raise ValueError("Widths must be positive numeric measurements")
            checked = _validated_record(record, _size(record["image_size"]))
            width = float(checked["width_um"])
            if not math.isfinite(width) or width <= 0:
                raise ValueError("Diameter must be finite and greater than zero")
            calibration = Calibration(**checked["calibration"])
            _validate_evidence(record, calibration)
            grouped[kind].append((number, width))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Measurement {number}: {exc}") from exc
    result = dict(composition=composition, microwire=microwire, selected_rows=selected,
                  core=None, outer=None)
    for kind, measurements in grouped.items():
        if measurements:
            values = [value for _, value in measurements]
            result[kind] = dict(mean_um=mean(values), count=len(values),
                                std_sample_um=stdev(values) if len(values) > 1 else None,
                                min_um=min(values), max_um=max(values),
                                measurement_numbers=[number for number, _ in measurements])
    if result["core"] and result["outer"] and result["core"]["mean_um"] > result["outer"]["mean_um"]:
        raise ValueError("Reviewed mean core diameter cannot exceed mean outer diameter")
    return result


__all__ = ["summarize_reviewed_diameters"]
