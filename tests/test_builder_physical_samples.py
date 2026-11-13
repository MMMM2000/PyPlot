"""Synthetic identity provenance, persistence and analysis checks."""
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from microwire_data_builder import core, safe_codec
from microwire_data_builder.physical_samples import (
    PHYSICAL_SAMPLE_COLUMN, measurement_identity_ledger, parse_identity_ledger,
    validated_physical_sample,
)
from microwire_eda.core import _aggregate_repeated_measurements


def identity(uid="11111111-1111-4111-8111-111111111111"):
    return dict(composition="Ni50Fe27Ga23", wire="12/2", label="s1", uid=uid)


def record(path, uid="11111111-1111-4111-8111-111111111111"):
    return core.MiniDmaRecord(path=Path(path), sample="Ni50Fe27Ga23 12/2", data=pd.DataFrame(),
                              key=("Ni50Fe27Ga23", 12, 2), physical_sample=identity(uid) if uid else None)


@pytest.mark.parametrize("value", [None, [], {}, {"composition": "Ni", "wire": "12/2", "label": "s1", "uid": []},
    {"composition": "Ni", "wire": "12/2", "label": "s0"}])
def test_malformed_identity_is_not_promoted(value):
    assert validated_physical_sample(value) is None


def test_identity_must_match_source_wire():
    assert validated_physical_sample(identity(), composition="Other") is None
    assert validated_physical_sample(identity(), wire="12/3") is None
    assert validated_physical_sample(identity(), wire="012_02")["uid"] == "11111111-1111-4111-8111-111111111111"


def test_annealing_metadata_preserves_explicit_identity_and_content_hash(tmp_path):
    records = []
    for uid in ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"):
        folder = tmp_path / uid
        folder.mkdir()
        path = folder / "measurement.txt"
        path.write_text("identical synthetic bytes")
        (folder / "metadata.json").write_text(json.dumps(dict(schema="current_annealing_logger_metadata_v1",
            composition="Ni50Fe27Ga23", microwire="12/2", data_file="measurement.txt", physical_sample=identity(uid))))
        metadata = core._metadata_from_path(path)
        assert (metadata.draw_x, metadata.piece_y) == (12, 2)
        assert metadata.physical_sample["uid"] == uid
        records.append(SimpleNamespace(path=path, metadata=metadata))
    assert records[0].metadata.measurement_id == records[1].metadata.measurement_id
    ledger = parse_identity_ledger(measurement_identity_ledger(records))
    assert [entry["physical_sample"]["uid"] for entry in ledger] == ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"]
    restored = safe_codec.decode_envelope(safe_codec.encode_envelope(records[0].metadata))
    assert restored.physical_sample == identity()


def test_tma_payload_and_lazy_table_restore_keep_per_source_identity():
    from microwire_data_builder.ui import _mini_dma_records_to_frame, _mini_dma_records_from_project_table
    records = [record("synthetic/run-a"), record("synthetic/run-b", "22222222-2222-4222-8222-222222222222"), record("synthetic/legacy", None)]
    payload = safe_codec.decode_envelope(safe_codec.encode_envelope(records))
    assert [r.physical_sample for r in payload] == [r.physical_sample for r in records]
    table = _mini_dma_records_to_frame(records)
    restored = _mini_dma_records_from_project_table(table)
    assert {str(r.path): r.physical_sample for r in restored} == {str(r.path): r.physical_sample for r in records}
    table[PHYSICAL_SAMPLE_COLUMN] = "malformed"
    assert all(r.physical_sample is None for r in _mini_dma_records_from_project_table(table))


def test_legacy_tma_safe_payload_missing_new_field_still_loads():
    encoded = safe_codec.encode_value(record("legacy", None))
    # Recreate an older encoded dataclass with no additive identity field.
    state = safe_codec.decode_value(encoded["state"])
    state.pop("physical_sample")
    encoded["state"] = safe_codec.encode_value(state)
    assert safe_codec.decode_value(encoded).physical_sample is None


@pytest.mark.parametrize("mode", ["per_wire_median", "per_wire_best"])
def test_eda_partitions_explicit_pieces_and_preserves_unknown_rows(mode):
    rows = []
    for index, uid in enumerate(("11111111-1111-4111-8111-111111111111", "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", None, None)):
        rows.append({"Composition": "Ni50Fe27Ga23", "Microwire": "12/2", "value": index,
                     PHYSICAL_SAMPLE_COLUMN: measurement_identity_ledger([record(f"run-{index}", uid)])})
    result = _aggregate_repeated_measurements(pd.DataFrame(rows), mode)
    assert list(result.measurement_count) == [2, 1, 1, 1]
    ledger = parse_identity_ledger(result.iloc[0][PHYSICAL_SAMPLE_COLUMN])
    assert len(ledger) == 2
    assert all(entry["physical_sample"]["uid"] == "11111111-1111-4111-8111-111111111111" for entry in ledger)


@pytest.mark.parametrize("ledger", ["bad-json", json.dumps([{"source": "bad", "physical_sample": {"uid": "fake"}}])])
def test_eda_malformed_provenance_is_not_merged(ledger):
    frame = pd.DataFrame([{"Composition": "Ni", "Microwire": "12/2", PHYSICAL_SAMPLE_COLUMN: ledger} for _ in range(2)])
    assert list(_aggregate_repeated_measurements(frame, "per_wire_median").measurement_count) == [1, 1]


def test_eda_mixed_wire_ledger_stays_independent():
    mixed = measurement_identity_ledger([record("a"), record("b", "22222222-2222-4222-8222-222222222222")])
    frame = pd.DataFrame([{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2", PHYSICAL_SAMPLE_COLUMN: mixed} for _ in range(2)])
    assert list(_aggregate_repeated_measurements(frame, "per_wire_median").measurement_count) == [1, 1]


def test_builder_wire_table_retains_both_piece_sources(tmp_path):
    records = [record("synthetic/run-a"), record("synthetic/run-b", "22222222-2222-4222-8222-222222222222")]
    result = core.build_database(core.BuilderConfig(fabrication_files=[], annealing_files=[], output_dir=tmp_path,
        make_plots=False, export_formats=(), plot_backends=()), mini_dma_records=records,
        fabrication_index=core.FabricationIndex(), skip_exports=True)
    assert len(result.dataframe) == 1  # Assembly remains source-wire level.
    assert result.dataframe.iloc[0]["Microwire"] == "12/2"
    ledger = parse_identity_ledger(result.dataframe.iloc[0][PHYSICAL_SAMPLE_COLUMN])
    assert {entry["physical_sample"]["uid"] for entry in ledger} == {"11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"}


@pytest.mark.parametrize("column,value", [("sample_uid", "not-a-uuid"), ("sample_uid", 123),
    ("sample_uid", []), (PHYSICAL_SAMPLE_COLUMN, []), (PHYSICAL_SAMPLE_COLUMN, 123)])
def test_eda_invalid_typed_evidence_never_falls_back_to_wire_aggregation(column, value):
    frame = pd.DataFrame([{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2", column: value} for _ in range(2)])
    assert list(_aggregate_repeated_measurements(frame, "per_wire_median").measurement_count) == [1, 1]


def test_eda_same_uuid_conflicting_labels_stay_separate():
    a, b = record("a"), record("b")
    b.physical_sample["label"] = "s2"
    frame = pd.DataFrame([{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2",
        PHYSICAL_SAMPLE_COLUMN: measurement_identity_ledger([item])} for item in (a, b)])
    assert list(_aggregate_repeated_measurements(frame, "per_wire_median").measurement_count) == [1, 1]


def test_eda_identity_source_mismatch_stays_separate():
    frame = pd.DataFrame([{"Composition": "Other", "Microwire": "12/2",
        PHYSICAL_SAMPLE_COLUMN: measurement_identity_ledger([record("a")])} for _ in range(2)])
    assert list(_aggregate_repeated_measurements(frame, "per_wire_median").measurement_count) == [1, 1]


def test_uuid_validation_and_normalization():
    assert validated_physical_sample(dict(identity(), uid="not-a-uuid")) is None
    assert validated_physical_sample(dict(identity(), uid="{11111111-1111-4111-8111-111111111111}"))["uid"] == identity()["uid"]



def test_lazy_table_duplicate_source_claims_are_not_last_wins():
    from microwire_data_builder.ui import _mini_dma_records_to_frame, _mini_dma_records_from_project_table
    table = _mini_dma_records_to_frame([record("same")])
    table[PHYSICAL_SAMPLE_COLUMN] = measurement_identity_ledger([record("same"), record("same", "22222222-2222-4222-8222-222222222222")])
    assert _mini_dma_records_from_project_table(table)[0].physical_sample is None


def test_lazy_table_identity_must_match_wire():
    from microwire_data_builder.ui import _mini_dma_records_to_frame, _mini_dma_records_from_project_table
    table = _mini_dma_records_to_frame([record("same")])
    ledger = parse_identity_ledger(table.iloc[0][PHYSICAL_SAMPLE_COLUMN])
    ledger[0]["physical_sample"]["wire"] = "99/9"
    table[PHYSICAL_SAMPLE_COLUMN] = json.dumps(ledger)
    assert _mini_dma_records_from_project_table(table)[0].physical_sample is None


def test_eda_partial_wire_summary_does_not_claim_single_piece():
    from microwire_data_builder.physical_samples import PHYSICAL_SAMPLE_SCOPE_COLUMN
    frame = pd.DataFrame([{"Composition": "Ni50Fe27Ga23", "Microwire": "12/2",
        PHYSICAL_SAMPLE_SCOPE_COLUMN: "source_wire_summary",
        PHYSICAL_SAMPLE_COLUMN: measurement_identity_ledger([record("a")])} for _ in range(2)])
    assert list(_aggregate_repeated_measurements(frame, "per_wire_median").measurement_count) == [1, 1]



@pytest.mark.parametrize("column", ["Physical sample ID", "Sample ID", "specimen"])
def test_eda_explicit_piece_labels_without_uuid_are_unknown_evidence(column):
    frame = pd.DataFrame([{"Composition": "Ni", "Microwire": "12/2", column: label} for label in ("s1", "s2")])
    assert list(_aggregate_repeated_measurements(frame, "per_wire_median").measurement_count) == [1, 1]


def test_preview_cache_distinguishes_explicit_pieces_with_identical_data():
    from microwire_data_builder.physical_samples import measurement_preview_cache_key
    first = SimpleNamespace(path=Path("first"), metadata=SimpleNamespace(measurement_id="same-content", physical_sample=identity()))
    second = SimpleNamespace(path=Path("second"), metadata=SimpleNamespace(measurement_id="same-content", physical_sample=identity("22222222-2222-4222-8222-222222222222")))
    assert measurement_preview_cache_key(first) != measurement_preview_cache_key(second)
    second.path = first.path
    assert measurement_preview_cache_key(first) != measurement_preview_cache_key(second)
    second.metadata.physical_sample = identity()
    assert measurement_preview_cache_key(first) == measurement_preview_cache_key(second)
    first.metadata.physical_sample = None
    assert measurement_preview_cache_key(first) == "same-content"



def test_lazy_table_conflicting_source_claims_across_rows_are_unverified():
    from microwire_data_builder.ui import _mini_dma_records_to_frame, _mini_dma_records_from_project_table
    tables = [_mini_dma_records_to_frame([record("same", uid)]) for uid in
              ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", "11111111-1111-4111-8111-111111111111")]
    restored = _mini_dma_records_from_project_table(pd.concat(tables, ignore_index=True))
    assert len(restored) == 1
    assert restored[0].physical_sample is None


def test_eda_conflicting_label_aliases_do_not_merge():
    frame = pd.DataFrame([{"Composition": "Ni", "Microwire": "12/2", "sample_uid": identity()["uid"],
                          "Physical sample ID": "s1", "specimen": label} for label in ("s1", "s2")])
    assert list(_aggregate_repeated_measurements(frame, "per_wire_median").measurement_count) == [1, 1]
