from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from microwire_data_builder.legacy_json_spool import (
    CHUNK_BYTES,
    SpoolLimits,
    decode_base64_json_member_to_file,
    spool_legacy_project,
)
from microwire_data_builder.safe_codec import SafeCodecError


def _write_project(path: Path, sections: str, *, extra: str = "") -> None:
    path.write_text(
        '{"kind":"MicrowireDataBuilder","version":1,"saved_at":"2026-07-15T12:00:00Z",'
        f'"sections":{sections}{extra}}}',
        encoding="utf-8",
    )


def test_spool_splits_state_table_and_payload_without_pickle_execution(tmp_path: Path) -> None:
    pickle_bytes = b"not executed: \x80\x04test" * 100
    encoded = base64.b64encode(pickle_bytes).decode("ascii")
    source = tmp_path / "legacy.pydpj"
    _write_project(
        source,
        json.dumps(
            {
                "annealing": {
                    "columns": ["Composition", "State"],
                    "rows": [["Ni50Fe27Ga23", "No transition"]],
                    "index": [7],
                    "selected_columns": ["State"],
                    "payloads": {
                        "records": {"encoding": "pickle-base64", "value": encoded},
                        "safe": {"encoding": "microwire-json", "version": 2, "value": {}},
                    },
                }
            },
            ensure_ascii=False,
        ),
    )

    result = spool_legacy_project(source, tmp_path / "spool")

    assert result.metadata["kind"] == "MicrowireDataBuilder"
    assert result.source_bytes == source.stat().st_size
    assert result.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.max_internal_buffer <= CHUNK_BYTES
    section = result.sections[0]
    assert json.loads(section.state_path.read_text("utf-8")) == {
        "selected_columns": ["State"]
    }
    assert json.loads(section.table_path.read_text("utf-8")) == {
        "columns": ["Composition", "State"],
        "rows": [["Ni50Fe27Ga23", "No transition"]],
        "index": [7],
    }
    payload = section.payloads["records"]
    assert payload.encoding == "pickle-base64"
    assert payload.pickle_path is not None
    assert payload.pickle_path.read_bytes() == pickle_bytes
    assert payload.encoded_bytes == len(encoded)
    assert payload.decoded_bytes == len(pickle_bytes)
    assert payload.sha256 == hashlib.sha256(pickle_bytes).hexdigest()
    assert section.payloads["safe"].pickle_path is None


def test_spool_preserves_case_distinct_diameter_data_keys(tmp_path: Path) -> None:
    source = tmp_path / "diameters.pydpj"
    _write_project(
        source,
        json.dumps({
            "fabrication": {
                "columns": ["d (µm)", "D (µm)"],
                "rows": [{"d (µm)": 12.5, "D (µm)": 19.0}],
                "index": [0],
                "extra": {"diameters": {"d (µm)": 12.5, "D (µm)": 19.0}},
            }
        }, ensure_ascii=False),
    )

    result = spool_legacy_project(source, tmp_path / "spool-diameters")

    section = result.sections[0]
    table = json.loads(section.table_path.read_text("utf-8"))
    state = json.loads(section.state_path.read_text("utf-8"))
    assert table["rows"][0] == {"d (µm)": 12.5, "D (µm)": 19.0}
    assert state["extra"]["diameters"] == {"d (µm)": 12.5, "D (µm)": 19.0}


def test_spool_preserves_exact_unicode_diameter_keys_in_top_level_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "exact-diameters.pydpj"
    _write_project(
        source,
        '{"fabrication":{"d (\\u00b5m)":12.5,"D (\\u00b5m)":19.0}}',
    )

    result = spool_legacy_project(source, tmp_path / "exact-diameter-stage")

    state = json.loads(result.sections[0].state_path.read_text("utf-8"))
    assert state == {"d (µm)": 12.5, "D (µm)": 19.0}


@pytest.mark.parametrize(
    "sections",
    [
        '{"annealing":{},"ANNEALING":{}}',
        '{"annealing":{"payloads":{"records":{},"RECORDS":{}}}}',
    ],
)
def test_structural_section_and_payload_ids_reject_case_collisions(
    tmp_path: Path, sections: str
) -> None:
    source = tmp_path / "structural-collision.pydpj"
    _write_project(source, sections)

    with pytest.raises(SafeCodecError, match="Case-colliding"):
        spool_legacy_project(source, tmp_path / "structural-stage")


def test_base64_member_incrementally_unescapes_json_string(tmp_path: Path) -> None:
    raw = b"abcdef/012345"
    encoded = base64.b64encode(raw).decode("ascii")
    # All three escapes are valid JSON and decode back to base64 ASCII.
    escaped = encoded.replace("/", r"\/").replace("Y", r"\u0059")
    envelope = tmp_path / "envelope.json"
    envelope.write_text(
        '{"encoding":"pickle-base64","value":"' + escaped + '"}', encoding="utf-8"
    )
    output = tmp_path / "payload.pkl"

    counts = decode_base64_json_member_to_file(
        envelope, "value", output, max_encoded=1000, max_decoded=1000
    )

    assert output.read_bytes() == raw
    assert counts == (len(encoded), len(raw), hashlib.sha256(raw).hexdigest())


def test_base64_rescan_reports_bounded_progress_heartbeats(tmp_path: Path) -> None:
    raw = b"heartbeat" * 20_000
    envelope = tmp_path / "large-envelope.json"
    envelope.write_text(json.dumps({
        "encoding": "pickle-base64",
        "value": base64.b64encode(raw).decode("ascii"),
    }), encoding="utf-8")
    output = tmp_path / "large-payload.pkl"
    heartbeats: list[tuple[int, int]] = []

    decode_base64_json_member_to_file(
        envelope,
        "value",
        output,
        max_encoded=1_000_000,
        max_decoded=1_000_000,
        progress=lambda done, total: heartbeats.append((done, total)),
    )

    assert output.read_bytes() == raw
    assert any(0 < done < total for done, total in heartbeats)
    assert heartbeats[-1] == (envelope.stat().st_size, envelope.stat().st_size)


def test_base64_rejects_concatenated_data_after_chunk_boundary_padding(tmp_path: Path) -> None:
    # The first independently valid stream ends exactly at the decoder flush boundary.
    first = base64.b64encode(b"x" * 49_150).decode("ascii")
    assert len(first) == CHUNK_BYTES and first.endswith("=")
    envelope = tmp_path / "bad-envelope.json"
    envelope.write_text(json.dumps({"value": first + "YQ=="}), encoding="utf-8")
    output = tmp_path / "bad.pkl"

    with pytest.raises(SafeCodecError, match="after base64 padding"):
        decode_base64_json_member_to_file(
            envelope, "value", output, max_encoded=100_000, max_decoded=100_000
        )

    assert not output.exists()


@pytest.mark.parametrize(
    "document,match",
    [
        ('{"kind":"MicrowireDataBuilder","kind":"x","version":1,"sections":{}}', "Duplicate"),
        ('{"kind":"MicrowireDataBuilder","Kind":"x","version":1,"sections":{}}', "Case-colliding"),
        ('{"kind":"MicrowireDataBuilder","version":01,"sections":{}}', "Malformed number"),
        ('{"kind":"MicrowireDataBuilder","version":true,"sections":{}}', "not a legacy Builder"),
        ('{"kind":"MicrowireDataBuilder","version":1,"sections":{"a":{"x":tru}}}', "Malformed literal"),
        ('{"kind":"MicrowireDataBuilder","version":1,"sections":{"a":{"x":[1}}}}', "array delimiter"),
        ('{"kind":"MicrowireDataBuilder","version":1,"sections":{}} trailing', "trailing data"),
        ('{"kind":"MicrowireDataBuilder","version":1,"sections":{"a":{"x":{"q":1,"q":2}}}}', "Duplicate"),
        ('{"kind":"MicrowireDataBuilder","version":1,"sections":{"a":{"x":"\\q"}}}', "invalid string escape"),
    ],
)
def test_spool_rejects_non_strict_json_and_removes_partial_stage(
    tmp_path: Path, document: str, match: str
) -> None:
    source = tmp_path / "bad.pydpj"
    source.write_bytes(document.encode("utf-8"))
    staging = tmp_path / "stage"

    with pytest.raises(SafeCodecError, match=match):
        spool_legacy_project(source, staging)

    assert not staging.exists()


def test_spool_rejects_invalid_utf8_depth_nodes_sizes_and_cancel(tmp_path: Path) -> None:
    common = b'{"kind":"MicrowireDataBuilder","version":1,"sections":'
    cases = [
        (common + b'{"a":{"x":"\xff"}}}', SpoolLimits(), "invalid UTF-8"),
        (common + b'{"a":{"x":[[[[1]]]]}}}', SpoolLimits(depth=3), "nesting-depth"),
        (common + b'{"a":{"x":[1,2,3]}}}', SpoolLimits(nodes=5), "node-count"),
        (common + b'{"a":{"x":"0123456789"}}}', SpoolLimits(state_bytes=8), "safe limit"),
    ]
    for index, (document, limits, match) in enumerate(cases):
        source = tmp_path / f"bad-{index}.pydpj"; source.write_bytes(document)
        with pytest.raises(SafeCodecError, match=match):
            spool_legacy_project(source, tmp_path / f"stage-{index}", limits=limits)

    source = tmp_path / "cancel.pydpj"
    _write_project(source, '{"annealing":{"rows":[' + ",".join("0" for _ in range(50000)) + "]}}")
    with pytest.raises(SafeCodecError, match="cancelled"):
        spool_legacy_project(source, tmp_path / "cancel-stage", cancelled=lambda: True)


def test_large_value_uses_fixed_internal_buffer_and_reports_progress(tmp_path: Path) -> None:
    # Scaled CI fixture: substantially larger than the parser buffer while cheap to run.
    source = tmp_path / "large.pydpj"
    large = "A" * (CHUNK_BYTES * 12 + 37)
    _write_project(source, json.dumps({"annealing": {"note": large}}))
    progress: list[tuple[str, int, int]] = []

    result = spool_legacy_project(
        source, tmp_path / "large-stage", progress=lambda *event: progress.append(event)
    )

    assert result.max_internal_buffer <= CHUNK_BYTES
    assert result.source_bytes > CHUNK_BYTES * 12
    assert progress[-1] == ("spool", result.source_bytes, result.source_bytes)
    state = result.sections[0].state_path
    assert state.stat().st_size > CHUNK_BYTES * 12
    assert json.loads(state.read_text("utf-8"))["note"] == large
