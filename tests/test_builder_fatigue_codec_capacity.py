from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from microwire_data_builder import safe_codec
from microwire_data_builder.core import MiniDmaRecord


pytestmark = pytest.mark.serial


def _stream(value):
    blobs = {}

    def sink(buffer):
        digest = hashlib.sha256(buffer).hexdigest()
        blobs[digest] = bytes(buffer)
        return digest, len(buffer)

    encoded = json.loads("".join(
        safe_codec.iterencode_envelope_with_blobs(value, sink)
    ))
    return encoded, blobs


def test_full_fatigue_shape_roundtrips_as_one_record_with_all_fields() -> None:
    # Native acquisition shape: 1,173,582 rows and 44 logger/scientific fields.
    # Compact synthetic dtypes keep regression memory modest; the full float64
    # acquisition is additionally validated on a disposable project artifact.
    count = 1_173_582
    base = np.arange(count, dtype=np.uint32)
    data = {f"field_{i}": ((base + i) % 251).astype(np.uint8) for i in range(40)}
    data.update(
        current_mA=base.astype(np.float64) / 10_000,
        strain_pct=base.astype(np.float64) / 1_000_000,
        phase=pd.Series(np.where(base % 2, "current_ramp", "current_limit_unwind"), dtype="str"),
        label=pd.Series(np.where(base % 3, "20 MPa", "40 MPa"), dtype=object),
    )
    frame = pd.DataFrame(data)
    frame.index = pd.RangeIndex(7, 7 + count * 2, 2, name="original_sample")
    record = MiniDmaRecord(
        path=Path("copied-fatigue"), sample="Ni50Fe25Ga25 5/1", data=frame,
        key=("Ni50Fe25Ga25", 5, 1), label="full history",
        strain_summary=("reviewed reference",), transition_summary=(),
    )
    encoded, blobs = _stream([record])
    restored = safe_codec.decode_record_list_envelope(
        encoded, blob_resolver=lambda digest, _size: blobs[digest],
    )
    assert len(restored) == 1
    assert restored[0].key == record.key
    assert restored[0].strain_summary == record.strain_summary
    assert frame.size == 51_637_608
    pd.testing.assert_frame_equal(restored[0].data, frame)


def test_columnar_cell_limit_is_checked_before_reading_any_data_blob(monkeypatch) -> None:
    frame = pd.DataFrame({"current": [1., 2., 3.], "strain": [0., .1, .2]})
    encoded, blobs = _stream(frame)
    monkeypatch.setattr(safe_codec, "MAX_COLUMNAR_DATAFRAME_CELLS", 6)
    pd.testing.assert_frame_equal(
        safe_codec.decode_envelope(encoded, blob_resolver=lambda digest, _: blobs[digest]),
        frame,
    )
    monkeypatch.setattr(safe_codec, "MAX_COLUMNAR_DATAFRAME_CELLS", 5)

    def unexpected_blob(*_):
        pytest.fail("An oversized frame must fail before reading/writing buffers")

    with pytest.raises(safe_codec.SafeCodecError, match="columnar pandas DataFrame"):
        list(safe_codec.iterencode_envelope_with_blobs(frame, unexpected_blob))
    with pytest.raises(safe_codec.SafeCodecError, match="columnar pandas DataFrame"):
        safe_codec.decode_envelope(encoded, blob_resolver=unexpected_blob)


@pytest.mark.parametrize("guard,message", [
    ("MAX_DECODE_ARRAY_ITEMS", "array item budget"),
    ("MAX_DECODE_BYTES", "byte budget"),
])
def test_aggregate_buffer_limits_prevent_second_blob_allocation(monkeypatch, guard, message) -> None:
    encoded, blobs = _stream([np.arange(3, dtype=np.uint8), np.arange(3, 6, dtype=np.uint8)])
    monkeypatch.setattr(safe_codec, guard, 5)
    resolved = []

    def resolver(digest, _size):
        resolved.append(digest)
        return blobs[digest]

    with pytest.raises(safe_codec.SafeCodecError, match=message):
        safe_codec.decode_envelope(encoded, blob_resolver=resolver)
    assert len(resolved) == 1


def test_array_capacity_does_not_raise_recursive_container_or_per_array_limits(monkeypatch) -> None:
    encoded, _ = _stream(np.arange(3, dtype=np.uint8))
    monkeypatch.setattr(safe_codec, "MAX_NDARRAY_ITEMS", 2)
    with pytest.raises(safe_codec.SafeCodecError, match="numpy array"):
        safe_codec.decode_envelope(encoded, blob_resolver=lambda *_: pytest.fail("oversized array"))
    monkeypatch.setattr(safe_codec, "MAX_DECODE_ITEMS", 2)
    with pytest.raises(safe_codec.SafeCodecError, match="item budget"):
        safe_codec.decode_envelope(safe_codec.encode_envelope([1, 2, 3]))


def test_duplicate_labels_missing_values_and_non_range_index_are_preserved() -> None:
    frame = pd.DataFrame({
        "a": pd.Series([1, pd.NA, 3], dtype="Int64"),
        "b": [1., np.nan, 3.],
        "phase": pd.Series(["heating", pd.NA, "cooling"], dtype="str"),
    })
    frame.columns = pd.Index(["signal", "signal", "phase"], name="channel")
    frame.index = pd.Index([5, 5, 8], name="source row")
    encoded, blobs = _stream(frame)
    pd.testing.assert_frame_equal(
        safe_codec.decode_envelope(encoded, blob_resolver=lambda digest, _: blobs[digest]),
        frame,
    )
