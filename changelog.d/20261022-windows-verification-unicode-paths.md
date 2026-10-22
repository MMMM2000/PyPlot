2026-10-22 13:00 UTC

- Allow Windows verification from accented user-profile/workspace paths when their short-path aliases are ASCII; continue rejecting unusable temp paths without falling back to shared storage.
- Default Windows parallel verification to ordered load scheduling so the existing Qt collection-order guard remains effective; keep explicit distribution overrides available.
