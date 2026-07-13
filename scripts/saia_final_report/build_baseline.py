"""Create a package-preserving SAIA final-report baseline from the half-year DOCX."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile
import zipfile


DOCUMENT_XML = "word/document.xml"
OLD_TITLE_RUNS = (
    "POLROČná",
    " správ",
    "a",
    " z\u00a0pobytu",
)
NEW_TITLE_RUNS = (
    "ZÁVEREČNÁ",
    " SPRÁV",
    "A",
    " Z\u00a0POBYTU",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _replace_once(payload: bytes, old: str, new: str) -> bytes:
    old_bytes = b">" + old.encode("utf-8") + b"<"
    new_bytes = b">" + new.encode("utf-8") + b"<"
    count = payload.count(old_bytes)
    if count != 1:
        raise ValueError(f"Expected exactly one {old!r} title run, found {count}.")
    return payload.replace(old_bytes, new_bytes, 1)


def patch_title(document_xml: bytes) -> bytes:
    """Patch only the four text runs in the first title paragraph."""

    first_paragraph_end = document_xml.find(b"</w:p>")
    if first_paragraph_end < 0:
        raise ValueError("Could not locate the first Word paragraph.")
    split_at = first_paragraph_end + len(b"</w:p>")
    first_paragraph = document_xml[:split_at]
    remainder = document_xml[split_at:]
    for old, new in zip(OLD_TITLE_RUNS, NEW_TITLE_RUNS, strict=True):
        first_paragraph = _replace_once(first_paragraph, old, new)
    return first_paragraph + remainder


def build(source: Path, output: Path, expected_sha256: str | None) -> None:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("Output must differ from the retained source DOCX.")
    if expected_sha256:
        actual = sha256(source)
        if actual.casefold() != expected_sha256.casefold():
            raise ValueError(
                f"Retained source hash changed: expected {expected_sha256}, got {actual}."
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "r") as source_zip:
        source_parts = {info.filename: source_zip.read(info.filename) for info in source_zip.infolist()}
        source_infos = source_zip.infolist()

    if DOCUMENT_XML not in source_parts:
        raise ValueError(f"Missing required DOCX package part: {DOCUMENT_XML}")
    final_parts = dict(source_parts)
    final_parts[DOCUMENT_XML] = patch_title(source_parts[DOCUMENT_XML])

    with tempfile.NamedTemporaryFile(
        prefix="saia-final-", suffix=".docx", dir=output.parent, delete=False
    ) as handle:
        temp_path = Path(handle.name)
    try:
        with zipfile.ZipFile(temp_path, "w") as output_zip:
            for info in source_infos:
                output_zip.writestr(info, final_parts[info.filename])
        temp_path.replace(output)
    finally:
        temp_path.unlink(missing_ok=True)

    with zipfile.ZipFile(output, "r") as final_zip:
        rebuilt_parts = {
            info.filename: final_zip.read(info.filename) for info in final_zip.infolist()
        }
    if set(rebuilt_parts) != set(source_parts):
        raise ValueError("DOCX package-part inventory changed unexpectedly.")
    changed = sorted(
        name for name in source_parts if source_parts[name] != rebuilt_parts[name]
    )
    if changed != [DOCUMENT_XML]:
        raise ValueError(f"Unexpected changed package parts: {changed}")

    print(f"source={source}")
    print(f"source_sha256={sha256(source)}")
    print(f"output={output}")
    print(f"output_sha256={sha256(output)}")
    print(f"changed_parts={','.join(changed)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    build(args.source, args.output, args.expected_sha256)


if __name__ == "__main__":
    main()
