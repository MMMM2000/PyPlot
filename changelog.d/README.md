# Changelog Fragments

Add one fragment file per branch/worktree change set.

File name recommendation:
- YYYYMMDD-HHMM-<short-slug>.md (UTC)

Content format:
- First line: YYYY-MM-DD HH:MM UTC
- Then bullet points describing user-facing changes or dependency/runtime updates.

CHANGELOG.md should be updated on main by consolidating merged fragments.

Merge command:
- `python scripts/merge_changelog_fragments.py --dry-run`
- `python scripts/merge_changelog_fragments.py --delete-merged`
