from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import PureWindowsPath
import re

import pytest

from data_logging.microscope_logger.session_naming import new_session_name


STAMP = datetime(2026, 11, 9, 13, 56, 57, 347713, tzinfo=timezone.utc)
TOKEN = "a5220a096c26402c917b57e905050a5c"


def name(composition=None, microwire=None, **kwargs):
    return new_session_name({"sample": {"composition": composition, "microwire": microwire}},
                            timestamp=STAMP, token=TOKEN, **kwargs)


def test_readable_sample_name_and_original_metadata_unchanged():
    metadata = {"sample": {"composition": "Ni50Fe27Ga23", "microwire": "10/4"}}
    assert new_session_name(metadata, timestamp=STAMP, token=TOKEN) == (
        "Ni50Fe27Ga23__10_4__20261109T135657347713Z_a5220a09")
    assert metadata["sample"]["microwire"] == "10/4"


@pytest.mark.parametrize("metadata", [{}, {"sample": None}, {"sample": "invalid"}, {"sample": {}}])
def test_unassigned_identity_is_explicit(metadata):
    assert new_session_name(metadata, timestamp=STAMP, token=TOKEN).startswith("Unassigned__Unassigned__")


@pytest.mark.parametrize("composition,microwire,expected", [
    (" CON. ", "LPT1.txt", "_CON___LPT1.txt__"),
    ("COM¹", "NUL", "_COM1___NUL__"),
    ("a<>:\"/\\|?*\x00\n b. ", "../10/4", "a_b__10_4__"),
    (" Ｎｉ５０Ｆｅ２７Ｇａ２３ ", "１０／４", "Ni50Fe27Ga23__10_4__"),
    ("Níkél", "piece 1", "Nikel__piece_1__"),
    ("..", ".", "Unassigned__Unassigned__"),
    ("\u200b\u202e", "   ", "Unassigned__Unassigned__"),
])
def test_windows_unsafe_names_and_unicode_are_normalized(composition, microwire, expected):
    result = name(composition, microwire)
    assert result.startswith(expected)
    assert len(PureWindowsPath(result).parts) == 1
    assert not re.search(r'[<>:"/\\|?*\x00-\x1f]', result)
    assert result.isascii()
    assert not result.endswith((" ", "."))


def test_names_are_bounded_even_for_long_sample_identities():
    result = name("A" * 1000, "B" * 1000)
    assert len(result) == 115
    assert result.startswith("A" * 48 + "__" + "B" * 32 + "__")
    assert result.endswith("_a5220a09")


def test_non_utc_timestamps_are_converted_to_utc():
    offset_stamp = STAMP.astimezone(timezone(timedelta(hours=1)))
    assert new_session_name({}, timestamp=offset_stamp, token=TOKEN) == new_session_name(
        {}, timestamp=STAMP, token=TOKEN)


@pytest.mark.parametrize("stamp", [datetime(2026, 11, 9), "20261109", 123])
def test_ambiguous_or_invalid_timestamp_is_rejected(stamp):
    with pytest.raises(ValueError, match="timezone-aware"):
        new_session_name({}, timestamp=stamp, token=TOKEN)


@pytest.mark.parametrize("token", ["", "abc", "a" * 9, "g" * 32, "../12345678", 123])
def test_token_cannot_insert_paths_or_unbounded_text(token):
    with pytest.raises(ValueError, match="hexadecimal"):
        new_session_name({}, timestamp=STAMP, token=token)


def test_token_accepts_uppercase_and_8_digit_form():
    assert new_session_name({}, timestamp=STAMP, token="ABCDEF01").endswith("_abcdef01")


def test_sanitization_collisions_keep_different_capture_suffixes():
    first = new_session_name({"sample": {"composition": "A/B", "microwire": "1"}},
                             timestamp=STAMP, token="12345678")
    second = new_session_name({"sample": {"composition": "A:B", "microwire": "1"}},
                              timestamp=STAMP, token="87654321")
    assert first != second
    assert first.rsplit("_", 1)[0] == second.rsplit("_", 1)[0]


def test_default_token_keeps_repeated_captures_distinct():
    assert new_session_name({}, timestamp=STAMP) != new_session_name({}, timestamp=STAMP)
