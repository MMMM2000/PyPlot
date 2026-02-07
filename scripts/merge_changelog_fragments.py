#!/usr/bin/env python3
"""Merge changelog fragments from changelog.d/ into CHANGELOG.md.

Expected fragment format:
- First non-empty line: timestamp (for example: 2026-02-07 20:10 UTC)
- Remaining lines: changelog bullet content
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FragmentEntry:
    path: Path
    heading: str
    body: str

    @property
    def rendered(self) -> str:
        return f"## {self.heading}\n\n{self.body.rstrip()}\n\n"


def _first_non_empty_line(lines: list[str]) -> int:
    for idx, line in enumerate(lines):
        if line.strip():
            return idx
    return -1


def _parse_fragment(path: Path) -> FragmentEntry:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    first = _first_non_empty_line(lines)
    if first == -1:
        raise ValueError("fragment is empty")

    timestamp = lines[first].strip()
    if timestamp.startswith("## "):
        timestamp = timestamp[3:].strip()
    if timestamp.startswith("# "):
        timestamp = timestamp[2:].strip()
    if not timestamp.endswith("UTC"):
        timestamp = f"{timestamp} UTC"

    body = "\n".join(lines[first + 1 :]).strip()
    if not body:
        raise ValueError("fragment has no body after timestamp")

    return FragmentEntry(path=path, heading=timestamp, body=body)


def _discover_fragments(fragments_dir: Path) -> list[Path]:
    if not fragments_dir.exists():
        return []
    paths: list[Path] = []
    for path in fragments_dir.glob("*.md"):
        if path.name.lower() == "readme.md":
            continue
        paths.append(path)
    return sorted(paths, key=lambda p: p.name, reverse=True)


def _insert_after_changelog_title(changelog_text: str, insert_text: str) -> str:
    title = "# Changelog"
    if not changelog_text.startswith(title):
        raise ValueError("CHANGELOG.md must start with '# Changelog'")

    lines = changelog_text.splitlines(keepends=True)
    insert_at = 1
    while insert_at < len(lines) and not lines[insert_at].strip():
        insert_at += 1

    if insert_at == 1:
        lines.insert(insert_at, "\n")
        insert_at += 1

    lines.insert(insert_at, insert_text)
    return "".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fragments-dir",
        default="changelog.d",
        help="Directory containing changelog fragments (default: changelog.d)",
    )
    parser.add_argument(
        "--changelog",
        default="CHANGELOG.md",
        help="Changelog file to update (default: CHANGELOG.md)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be merged without writing files",
    )
    parser.add_argument(
        "--delete-merged",
        action="store_true",
        help="Delete fragment files that were merged",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    fragments_dir = Path(args.fragments_dir)
    changelog_path = Path(args.changelog)

    fragment_paths = _discover_fragments(fragments_dir)
    if not fragment_paths:
        print(f"No fragment files found in {fragments_dir}.")
        return 0

    parsed: list[FragmentEntry] = []
    parse_errors: list[tuple[Path, str]] = []
    for path in fragment_paths:
        try:
            parsed.append(_parse_fragment(path))
        except ValueError as exc:
            parse_errors.append((path, str(exc)))

    if parse_errors:
        for path, reason in parse_errors:
            print(f"ERROR: {path}: {reason}")
        return 1

    changelog_text = changelog_path.read_text(encoding="utf-8")

    to_merge: list[FragmentEntry] = []
    already_present: list[FragmentEntry] = []
    for entry in parsed:
        if entry.rendered in changelog_text:
            already_present.append(entry)
        else:
            to_merge.append(entry)

    if already_present:
        print("Already present in changelog:")
        for entry in already_present:
            print(f"- {entry.path}")

    if not to_merge:
        print("No new fragments to merge.")
        return 0

    insert_text = "".join(entry.rendered for entry in to_merge)
    updated_text = _insert_after_changelog_title(changelog_text, insert_text)

    if args.dry_run:
        print("Would merge fragments:")
        for entry in to_merge:
            print(f"- {entry.path} -> {entry.heading}")
        print("Dry run: no files were changed.")
        return 0

    changelog_path.write_text(updated_text, encoding="utf-8")

    print(f"Merged {len(to_merge)} fragment(s) into {changelog_path}:")
    for entry in to_merge:
        print(f"- {entry.path} -> {entry.heading}")

    if args.delete_merged:
        for entry in to_merge:
            entry.path.unlink(missing_ok=True)
        print("Deleted merged fragment files.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
