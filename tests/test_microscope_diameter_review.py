"""Pure synthetic eligibility checks; no project, camera or settings are opened."""
from __future__ import annotations

import copy
import math

import pytest

from data_logging.microscope_logger.diameter_review import summarize_reviewed_diameters
from data_logging.microscope_logger.measurements import make_calibration, measure_line


COMPOSITION = "SyntheticAlloy"
WIRE = "12/2"
CONTEXT = dict(image_size=(400, 300), objective="4x", camera_id="synthetic-camera", zoom=1)


def record(width_um=10, *, kind="core", composition=COMPOSITION, wire="12_2"):
    calibration = make_calibration(100, 50, source="Synthetic reference length", **CONTEXT)
    result = measure_line((30, 30), (30 + width_um / 0.5, 30),
                          calibration=calibration, kind=kind, **CONTEXT)
    result.update(review_status="Reviewed", method="parallel_edges", sample={
        "composition": composition, "microwire": wire, "source_kind": "project",
        "source_path": "C:/synthetic/project.pydpj", "source_sha256": "a" * 64,
        "source_rows": [{"binding_type": "project_table_row", "section": "microscope",
                         "row_index": 3, "composition": composition, "microwire": wire}],
    }, calibration_evidence={"image_sha256": "b" * 64, "start": [10, 10], "end": [110, 10]})
    return result


def summarize(records, selected=None, **kwargs):
    return summarize_reviewed_diameters(records, kwargs.get("composition", COMPOSITION),
                                         kwargs.get("microwire", WIRE),
                                         list(range(1, len(records) + 1)) if selected is None else selected)


def test_selected_rows_produce_exact_per_kind_statistics_and_preserve_input():
    records = [record(10), record(14), record(20, kind="outer"), record(24, kind="outer"),
               {"kind": "apparent", "review_status": "Unreviewed"}]
    before = copy.deepcopy(records)
    result = summarize(records, [4, 2, 3, 1])
    assert result["selected_rows"] == [1, 2, 3, 4]
    assert result["composition"] == COMPOSITION and result["microwire"] == WIRE
    assert result["core"] == dict(mean_um=12, count=2, std_sample_um=math.sqrt(8),
                                  min_um=10, max_um=14, measurement_numbers=[1, 2])
    assert result["outer"] == dict(mean_um=22, count=2, std_sample_um=math.sqrt(8),
                                   min_um=20, max_um=24, measurement_numbers=[3, 4])
    assert records == before


@pytest.mark.parametrize("kind", ["core", "outer"])
def test_one_kind_and_one_measurement_is_allowed_without_invented_deviation(kind):
    result = summarize([record(11, kind=kind)])
    assert result[kind] == dict(mean_um=11, count=1, std_sample_um=None,
                                min_um=11, max_um=11, measurement_numbers=[1])
    assert result["outer" if kind == "core" else "core"] is None


@pytest.mark.parametrize("selection", [[], [0], [-1], [2], [1, 1], [True], [1.0], ["1"], "1", {1}])
def test_invalid_or_duplicate_measurement_numbers_are_rejected(selection):
    with pytest.raises(ValueError):
        summarize([record()], selection)


@pytest.mark.parametrize("composition,wire", [("AnotherAlloy", WIRE), (COMPOSITION, "12/3"),
                                             (COMPOSITION, "12/2 NG"), (COMPOSITION, "12/2 oe")])
def test_different_specimen_or_treatment_suffix_cannot_be_mixed(composition, wire):
    with pytest.raises(ValueError, match="different specimen"):
        summarize([record(), record(composition=composition, wire=wire)])


@pytest.mark.parametrize("wire", ["12_2", "012/02", "12-2", "12\\2", " 12 / 2 "])
def test_only_numeric_separator_normalization_preserves_exact_identity(wire):
    assert summarize([record(wire=wire)])["core"]["count"] == 1


@pytest.mark.parametrize("change", [
    {"review_status": "Unreviewed"}, {"review_status": "reviewed"}, {"review_status": None},
    {"kind": "apparent"}, {"kind": "unsupported"}, {"calibration_status": "Uncalibrated"},
    {"calibration": None}, {"width_um": None}, {"width_um": 10000},
    {"width_px": 10000}, {"width_um": float("nan")}, {"width_um": float("inf")},
    {"width_um": 0}, {"width_um": -1}, {"width_um": True},
    {"end": [400, 30]}, {"image_size": [100, 100]}, {"objective": "10x"}, {"zoom": 2},
])
def test_reviewed_flags_do_not_bypass_units_calibration_or_recorded_math(change):
    value = record()
    value.update(change)
    with pytest.raises(ValueError, match="Measurement 1"):
        summarize([value])


def test_reviewed_boolean_without_explicit_review_status_is_insufficient():
    value = record()
    value.pop("review_status")
    value["reviewed"] = True
    with pytest.raises(ValueError, match="Reviewed status"):
        summarize([value])


@pytest.mark.parametrize("evidence", [None, {}, {"image_sha256": "bad"},
    {"image_sha256": "B" * 64, "start": [10, 10], "end": [110, 10]},
    {"image_sha256": "b" * 64, "start": [10, 10], "end": [109, 10]},
    {"image_sha256": "b" * 64, "start": [10, 10], "end": [400, 10]},
])
def test_reference_evidence_and_geometry_are_required_before_backend_file_verification(evidence):
    value = record()
    value["calibration_evidence"] = evidence
    with pytest.raises(ValueError):
        summarize([value])


@pytest.mark.parametrize("change", [{"source_kind": "manual"}, {"source_sha256": "bad"},
                                    {"source_path": ""}, {"source_rows": []},
                                    {"matching_sources": "malformed"}])
def test_original_project_binding_is_required(change):
    value = record()
    value["sample"].update(change)
    with pytest.raises(ValueError, match="project row binding"):
        summarize([value])


def test_project_binding_can_be_retained_as_matching_source_and_keep_older_sha():
    value = record()
    project = copy.deepcopy(value["sample"])
    value["sample"] = dict(composition=COMPOSITION, microwire=WIRE, source_kind="fabrication",
                            matching_sources=[project])
    assert summarize([value])["core"]["mean_um"] == 10
    assert project["source_sha256"] == "a" * 64


@pytest.mark.parametrize("row_change", [{"composition": "OtherAlloy"}, {"microwire": "12/2 oe"},
                                        {"row_index": -1}, {"row_index": True}, {"section": ""}])
def test_project_row_binding_must_identify_the_selected_specimen(row_change):
    value = record()
    value["sample"]["source_rows"][0].update(row_change)
    with pytest.raises(ValueError, match="project row binding"):
        summarize([value])


def test_core_outer_order_is_checked_on_published_means():
    with pytest.raises(ValueError, match="core diameter cannot exceed"):
        summarize([record(30), record(20, kind="outer")])
    assert summarize([record(20), record(20, kind="outer")])["core"]["mean_um"] == 20


def test_mixed_pixel_and_micrometre_rows_cannot_be_published_together():
    calibrated = record()
    pixels = measure_line((10, 10), (30, 10), kind="core", **CONTEXT)
    pixels.update(sample=copy.deepcopy(calibrated["sample"]), review_status="Reviewed")
    with pytest.raises(ValueError, match="pixel-only"):
        summarize([calibrated, pixels])
