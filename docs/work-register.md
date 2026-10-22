# Task ownership and handoff

The master chat owns current scope, integration order and acceptance decisions.
Workers use focused branches/worktrees and return reviewable commits/PRs. Check
the master ledger and overlapping owners before editing shared surfaces. Keep
Origin/export helper ownership, README overlap, CI runner ownership and hardware
ownership explicit.

Keep mutable state in the active task's ledger under ignored `artifacts/` or its
handoff. Record these fields for each active scope:

| Field | Required evidence |
| --- | --- |
| Owner | Master and worker chat; responsible host/device/bench owner |
| Source | Exact worktree, branch, upstream, base commit and current head |
| Local state | Dirty/untracked work, stashes and relevant source/evidence copies |
| Review | Complete PR scope/URL; shared-file overlaps; integration owner |
| Inputs | Disposable-copy paths, source IDs, hashes and isolated storage/settings |
| Verification | Tested commit, commands, results, skips and unverified acceptance |
| Retention | Durable deliverables/recovery images, rebuildable caches, backups, unknowns |
| Next decision | Explicit remaining decision and who owns it |

Do not treat a branch, running application, remembered device state or old test
count as current evidence for another head. Do not merge or publish merely because
a worker's software checks pass. See `AGENTS.md` for the binding safety rules.
