from __future__ import annotations

from copy import deepcopy
import logging
from types import SimpleNamespace

import pandas as pd
import pytest

from microwire_data_builder import ui as builder_ui
from plotting.shared import transition_review as review_module
from plotting.shared import transition_review_adapters as adapter_module


LABELS = ["As", "Af", "Ms", "Mf"]


def _tma_import(tmp_path, monkeypatch, existing, target, *, auto_status="rejected"):
    run_dir = tmp_path / "synthetic_tma"
    run_dir.mkdir()
    (run_dir / "transition_review.json").write_text("{}", encoding="utf-8")
    frame = pd.DataFrame({"current_mA": [0.0, 10.0], "strain_pct": [0.0, 1.0]})
    fingerprint = adapter_module.tma_measurement_fingerprint(frame)
    payload = {
        "experiment_family": "tma",
        "measurement_fingerprint": fingerprint,
        "review_revision": 13,
        "targets": [{"target": {"stress_mpa": 250.0}, **target}],
    }
    entry = SimpleNamespace(
        sample="synthetic sample", run_label="synthetic run",
        target_label="250 MPa", status=auto_status,
        target_summary=SimpleNamespace(stress_mpa=250.0),
    )
    record = SimpleNamespace(path=run_dir, data=frame)
    key = builder_ui._mini_dma_review_record_id(record, entry.target_label)
    existing = deepcopy(existing)
    existing.update(
        measurement_fingerprint=fingerprint, portable_review_revision=13,
        portable_sidecar_path=str(run_dir / "transition_review.json"),
    )
    previous = existing.get("portable_review")
    if previous is not None:
        previous.update(
            measurement_fingerprint=fingerprint, portable_review_revision=13,
            portable_sidecar_path=str(run_dir / "transition_review.json"),
        )
    reviews = {key: existing}
    before = deepcopy(reviews)
    monkeypatch.setattr(review_module, "load_review", lambda _path: payload)
    monkeypatch.setattr(adapter_module, "tma_review_draft", lambda _path: {
        "measurement_fingerprint": fingerprint,
    })
    monkeypatch.setattr(builder_ui, "_mini_dma_transition_review_entries", lambda *_args: [entry])
    changed = builder_ui._import_portable_tma_reviews(
        [record], reviews, logging.getLogger(__name__),
    )
    return changed, reviews[key], before[key]


@pytest.mark.parametrize("clears", [None, [], LABELS, "As;Af;Ms;Mf"])
def test_legacy_no_transition_clear_formats_are_equivalent(clears):
    legacy = {"status": "no_transition"}
    modern = {"status": "no_transition", "values": {}}
    if clears is not None:
        modern["cleared_labels"] = clears
    before = deepcopy((legacy, modern))
    assert builder_ui._mini_dma_portable_review_semantics(legacy) == (
        builder_ui._mini_dma_portable_review_semantics(modern)
    )
    assert (legacy, modern) == before


def test_no_transition_with_final_thresholds_is_not_normalized():
    contradictory = {"status": "no_transition", "values": {"As": 20.0}}
    explicit = {**contradictory, "cleared_labels": LABELS}
    assert builder_ui._mini_dma_portable_review_semantics(contradictory) != (
        builder_ui._mini_dma_portable_review_semantics(explicit)
    )
    assert builder_ui._mini_dma_portable_review_semantics(contradictory) != (
        builder_ui._mini_dma_portable_review_semantics({"status": "no_transition"})
    )


def test_accepted_partial_clears_are_still_distinct():
    accepted = {"status": "accepted", "values": {"As": 20.0}}
    assert builder_ui._mini_dma_portable_review_semantics(accepted) != (
        builder_ui._mini_dma_portable_review_semantics({**accepted, "cleared_labels": ["Mf"]})
    )


def test_tma_import_retains_accepted_legacy_no_transition(tmp_path, monkeypatch):
    changed, imported, before = _tma_import(tmp_path, monkeypatch, {
        "status": "no_transition", "analysis_included": True,
        "auto_values_mA": {"As": 99.0}, "strain_reference": {"method": "saved"},
    }, {"status": "no_transition", "analysis_included": True, "cleared_labels": LABELS})
    assert changed is False
    assert imported == before
    assert "portable_conflict" not in imported


@pytest.mark.parametrize("target", [
    {"status": "manual_adjusted", "final_values": {"As": 20.0}},
    {"status": "excluded"},
    {"status": "no_transition", "final_values": {"As": 20.0}, "cleared_labels": LABELS},
])
def test_tma_import_keeps_genuine_decision_changes_as_conflicts(tmp_path, monkeypatch, target):
    changed, imported, before = _tma_import(
        tmp_path, monkeypatch, {"status": "no_transition", "analysis_included": True}, target,
    )
    assert changed is True
    assert imported["status"] == "needs_attention"
    assert imported["portable_conflict"] == "project_and_sidecar_differ"
    assert imported["project_review"] == before


def test_tma_import_retains_unchanged_conflict_analysis_context(tmp_path, monkeypatch):
    changed, imported, before = _tma_import(tmp_path, monkeypatch, {
        "status": "needs_attention", "analysis_included": False,
        "portable_conflict": "project_and_sidecar_differ",
        "project_review": {"status": "accepted", "values": {"As": 19.0}},
        "portable_review": {
            "status": "accepted", "values": {"As": 20.0},
            "auto_status": "partial", "auto_values_mA": {"As": 17.0},
            "strain_reference": {"method": "saved"},
        },
    }, {"status": "manual_adjusted", "final_values": {"As": 20.0},
        "auto_values": {"As": 22.0}, "strain_reference": {"method": "recomputed"}})
    assert changed is False
    assert imported == before


def test_ca_unchanged_conflict_updates_only_provenance():
    portable = {
        "status": "manual_adjusted", "final_values_mA": {"As1": 20.0},
        "content_identity": "synthetic-fingerprint", "portable_sidecar_path": "new.json",
        "portable_review_revision": 2, "auto_values_mA": {"As1": 99.0},
        "updated_at": "new computed timestamp",
    }
    previous = {**portable, "portable_sidecar_path": "old.json", "portable_review_revision": 1,
                "auto_values_mA": {"As1": 17.0}, "updated_at": "saved timestamp"}
    existing = {
        "status": "needs_attention", "included": False,
        "portable_conflict": "project_and_sidecar_differ",
        "project_review": {"status": "accepted_auto", "final_values_mA": {"As1": 19.0}},
        "portable_review": previous, "content_identity": "synthetic-fingerprint",
        "portable_sidecar_path": "old.json", "portable_review_revision": 1,
    }
    untouched = deepcopy((existing, portable))
    result = builder_ui._merge_portable_annealing_review(existing, portable)
    expected = deepcopy(existing)
    for key in ("content_identity", "portable_sidecar_path", "portable_review_revision"):
        expected[key] = expected["portable_review"][key] = portable[key]
    assert result == expected
    assert (existing, portable) == untouched


@pytest.mark.parametrize("change", [
    {"final_values_mA": {"As1": 21.0}},
    {"status": "excluded"},
    {"content_identity": "changed-fingerprint"},
    {"cleared_labels": ["Mf1"]},
])
def test_conflict_refresh_rejects_changed_decisions_or_source(change):
    previous = {
        "status": "manual_adjusted", "final_values_mA": {"As1": 20.0},
        "content_identity": "synthetic-fingerprint", "portable_review_revision": 1,
    }
    existing = {"portable_conflict": "project_and_sidecar_differ", "portable_review": previous}
    assert builder_ui._refresh_unchanged_portable_conflict(
        existing, {**previous, **change}, builder_ui._review_semantics, "content_identity",
    ) is None
