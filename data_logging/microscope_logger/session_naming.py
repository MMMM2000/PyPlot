"""Readable names for new capture folders; raw sample identities stay in JSON."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import re
import unicodedata
import uuid


_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


def _component(value: object, *, limit: int, fallback: str) -> str:
    """Use a short ASCII component safe in ordinary Windows paths."""
    text = "" if value is None else str(value)
    # Compatibility normalization handles full-width slashes/digits and device
    # names containing superscript digits before checking the Windows names.
    normalized = unicodedata.normalize("NFKC", text)
    ascii_text = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", ascii_text)
    cleaned = re.sub(r"_+", "_", cleaned).strip(" ._")[:limit].rstrip(" ._")
    if not cleaned or not cleaned.strip(".-"):
        return fallback
    if cleaned.partition(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned[:limit].rstrip(" .")


def new_session_name(metadata: Mapping[str, object], *, timestamp: datetime | None = None,
                     token: str | None = None) -> str:
    """Return one new folder name, not a path or a change to an older session.

    Format: ``composition__microwire__UTC_timestamp_uuid8``. Missing identities
    become ``Unassigned``. Identity components are bounded to 48 and 32 ASCII
    characters, so the entire name is at most 115 characters. The random suffix
    prevents sanitized or truncated sample identities sharing a capture folder;
    callers must still refuse to replace any existing destination.

    ``timestamp`` must be timezone aware. ``token`` may be an eight-digit or
    32-digit hexadecimal UUID token; injectable values support deterministic
    tests and let the session writer use its existing session ID.
    """
    stamp = datetime.now(timezone.utc) if timestamp is None else timestamp
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Session timestamp must be a timezone-aware datetime")
    identifier = uuid.uuid4().hex if token is None else token
    if not isinstance(identifier, str) or re.fullmatch(r"(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{32})", identifier) is None:
        raise ValueError("Session token must contain 8 or 32 hexadecimal digits")
    sample = metadata.get("sample")
    sample = sample if isinstance(sample, Mapping) else {}
    composition = _component(sample.get("composition"), limit=48, fallback="Unassigned")
    microwire = _component(sample.get("microwire"), limit=32, fallback="Unassigned")
    utc_stamp = stamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{composition}__{microwire}__{utc_stamp}_{identifier[:8].lower()}"
