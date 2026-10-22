# Artifact retention review — 22 October 2026

This is an inventory and retention proposal. No ignored artifacts were deleted,
moved or copied wholesale. Unknown ownership or backup status means preserve.

The prior measured inventory in the primary checkout at
`artifacts/repo-audit-20261022/inventory.json` reports **71,408,441,745 logical
bytes (66.5 GiB), 155,326 files, and one stat error**, measured at
2026-10-22 08:33 UTC. This is not physical disk allocation or a deletion estimate.
Its SHA-256 and the original measurement are recorded with the new inventory.
We did not repeat its expensive recursive byte scan.

The follow-up records 699 top-level entries using metadata and at most eight
direct child names per directory. Categories use conservative path-name evidence;
they do not certify every nested file. No secret values were inspected.

| Class | Entries | Evidence and action |
| --- | ---: | --- |
| Reproducible cache candidates | 39 | `uv-cache`, `uv_cache`, `pip-cache`, `mpl-cache*`, `mpl_cache*`, `pycache-*`; verify actual contents, active owner and rebuild route before any later deletion. |
| Research/report/video evidence | 123 | `esa_*`, `as_ea_*`, `swathi_*`, `si_report*`, `xrd_*`, `vacuum-demo`, `word_*`; preserve deliverables, sources and acceptance evidence. |
| Possible source/database/recovery state | 54 | `builder-storage*`, `*project*`, `*backup*`, `worker_salvage`; preserve pending owner/backup review. A local store may contain unique data. |
| Firmware/build/recovery ownership | 5 | `arduino-build`, `arduino-cache`, `i2c_scanner_h753zi`, `rva_demonstrator_development`, `pytest-stm32-firmware`; preserve binaries/recovery images until exact source, toolchain, intended board and backups are established. |
| Test scratch with possible retained sources | 277 | `pytest-*`, `tool-temp*`, `test-qsettings*`, basetemp roots; a directory name does not prove all contents disposable. Preserve until the owning task identifies retained evidence/source copies. |
| Completed Origin audit | 1 | `origin-audit-20261022` is durable audit evidence; preserve. |
| Unknown ownership/backup | 200 | Everything not established above; preserve. |

The full path/child-name inventory is retained locally as
`artifacts/sample-data-cleanup/artifact-retention.json` in the sample-data task's
managed worktree and copied as evidence into this guidance task's ignored
`artifacts/repository-guidance/`. It records unknown owner/backup state explicitly
and leaves per-directory recursive byte estimates unset. Reclassify entries using
owner evidence before cleanup, rather than treating this name-based review as
authorization. No worktree, branch, research source, source project or recovery
image is approved for deletion here.

The tracked `ttyV0`/`ttyV1` links pointed to stale `/dev/ttys021` and
`/dev/ttys022` endpoints. The emulator recreates its own local pair. Only those two
tracked links are removed and ignored; emulator functionality is preserved.
Historical `continue_here.md`/`TODO.md` content is archived under `docs/history/`
with clear navigation to current instructions and handoffs. Existing changelog
fragments remain untouched; consolidation belongs to release/integration.
