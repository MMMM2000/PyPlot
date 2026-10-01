# Microwire Data Builder integrity audit — 2026-09-30 UTC

This pass focuses on the paths required to update the microwire database: reading portable projects, discovering and importing measurements, preserving scientific/manual metadata, rebuilding Assemble, mapping pieces to videos, and publishing a rolling database. Fixes were developed from current `origin/main` in a separate worktree. The primary checkout and live database were not modified by the audit.

## Confirmed defects and fixes

| Area | Reproduced failure | Resulting behavior |
| --- | --- | --- |
| Large record collections | A complete collection exceeded the generic codec item limit even though individual measurements were valid; soft decoding could turn the failure into missing records. | Packaged measurement lists decode record by record. Ordinary per-record limits, supported-type checks, numeric blob hashes, cumulative project read limits and aggregate allocation limits remain enforced. Invalid modern automation payloads stop the update. Other safe package payload shapes remain compatible. |
| Record metadata | Preview grouping rewrote the stored sample/label objects. Dynamic treatment variants were omitted from safe serialization. | Previews normalize shallow copies, and the four affected record classes have an optional persisted variant field. Legacy hysteresis grouping recognizes treatments explicitly present in saved sample/temperature labels, without changing the original records. |
| Refresh preservation | Refresh removed records inside the input root before parsing; a skipped/unreadable source could erase a valid embedded curve. | Successful reimports replace their source record; unsuccessful imports retain the old record. VSM saved grouping and external-only references remain. Graph root replacement requires `prune_missing`; TMA keeps its existing active-run gating. |
| Source identity | Different Windows capitalization could duplicate one source. Missing source paths could collide as `None`. | Windows merges normalize path case. Records with no path retain separate fallback identities. Empty sample names do not collapse unrelated VSM rows. |
| Fabrication | A partial refresh replaced visible and raw fabrication indexes, losing other pieces. | Both indexes merge draw/piece metadata, preserve nonblank values absent in incoming data, and keep saved table fields absent in new spreadsheets. |
| Videos | Rebuilding video rows omitted saved readings/Fabrication context. Piece-length overrides were applied after cumulative lengths had already been calculated. | Video automation restores the project's Fabrication dependency, retains saved readings, and calculates cumulative lengths after overrides. It does not extract new readings from video. |
| Video intervals | Missing piece numbers were treated as contiguous, invalid lengths could produce impossible ranges, and integer rounding discarded fractional metres. | Missing preceding lengths, nonpositive/nonfinite lengths and impossible end lengths produce an unknown interval. Valid ranges retain fractional metres. For example, end 105.75 m, cumulative 18.84 m and piece length 12.56 m produce `86.91-99.47`. |
| Database promotion | A later manifest failure left a new project paired with the old manifest. Failure while preparing the second archive could leak the first temporary file. | Prepared archives support rollback of the project on reported promotion failure. Temporary archives are cleaned up; recovery copies remain when rollback cannot safely replace a newer external save. |
| Concurrent saves and path aliases | A long update could replace a newer save, and an incorrectly configured manifest path could overwrite the project. | Content hashes guard source/output/latest files and are rechecked immediately before project replacement. Project/manifest aliases are rejected. |
| Store isolation | Fixed staging/store directories could interfere across runs. Automation replaced only part of the process cache and cleared the caller's state on exit. | Each run owns unique temporary directories. All store caches, lazy loaders, blocked/pending state and transaction counters are isolated and restored on exit. Cleanup is restricted to the run's own directories. |
| Instructions | Documentation described retired pickle encoding and unsupported Fabrication/Video automation; the Praha template used an old TMA location. | Documentation describes safe packaged projects, supported commands, preservation/pruning behavior and review before promotion. The TMA template uses `Praha/data/TMA`. |

## Verification scope

The automated suites cover Builder core/UI, project packages, safe and legacy codecs, Universal Video Builder, launcher/automation, Assemble/Word export preparation, EDA, Košice Origin extraction, and transition-review audit/sidecars. New regression cases specifically reproduce the integrity failures above. A legacy preview test now supplies its own empty store instead of relying on an empty shared test directory.

The final run passed **706 tests**, including 35 new integrity regression cases. Tests run with Python 3.14.4, the frozen dependency lock, offscreen Qt, disposable projects and isolated test settings/stores. Syntax compilation and `git diff --check` also passed. No live instrument or controller was used.

An end-to-end check used a separate copy of the verified post-USB database (source SHA-256 `208c491db03fee053ce4f48fc932228528349918a55aac1c72cbba6a7a095f21`). It refreshed both VSM sections against an empty input directory, rebuilt Assemble, saved and reopened the package, verified every package entry, and compared every embedded record's metadata and numeric blob references. It also compared saved nonmeasurement values and manual/review metadata. This checks preservation during a refresh with no new readable sources; synthetic cases separately cover successful imports, unreadable sources and partial fabrication/video updates.

| Embedded family | Retained records |
| --- | ---: |
| Current annealing | 271 |
| DMA iso-stress | 2 |
| FMR | 10 |
| TMA | 71 |
| Shape-memory stress/strain | 51 |
| VSM hysteresis | 912 |
| VSM temperature scan | 75 |
| Total | 1,392 |

All these record fingerprints remained identical after accounting for the additive optional `variant=None` field. Saved VSM tables retained 26 hysteresis groups and 34 temperature-scan groups. Manual values, transition reviews and untouched section tables remained unchanged. Assemble rebuilt from 324 to 326 rows without losing any existing composition/microwire identities. The source copy remained byte-identical.

Disposable logs, package copies and machine-readable comparisons are under the worktree's ignored `artifacts/audit/`; the validation source was never the live `.pydpj`.

## Evidence limits and follow-up

- This is a broad software audit, not a proof that every possible defect is absent. Scientific transition choices and pressure-unit interpretation still require source evidence and review.
- Live Word/Origin OLE insertion/rendering and hardware behavior were not exercised. Their preparation logic and existing software regressions were included.
- Atomic replacement applies to each file. Rollback covers reported exceptions; a process/power loss between the project and manifest replacements is not a two-file filesystem transaction. Prepared archives provide recovery evidence for that case.
- The database already had three hysteresis table groups without embedded curves before the USB import: Ni46Fe27Ga23Cu2Co2 2/1, 2/5 and 2/7. Their source references are preserved; recovering those historical curves requires reconciling the referenced raw files.
- The two newly assembled rows are `Ni48Fe27Ga23Co1Cu1 1/1` and `1/5`, already present in the saved TMA section's run sources. Other families use the equivalent nominal formula spelling `Ni48Fe27Ga23Cu1Co1`. Both spellings and all source records are preserved; physical sample identity should be reconciled before combining these families or counting independent specimens in a report.
- Fabrication parameters read from video should be saved with frame/time/length evidence and the visible units. The manual/assisted frame-review workflow is a separate data update; this pass does not reintroduce the retired OCR engine.

## October 1 follow-up

A full source inventory and staged update exposed additional failures. Fabrication
matching accepted unrelated compositions sharing a draw number; automated refresh
did not restore both annealing and microscopy context; reference-only video refresh
could replace saved readings with empty lists. These paths now preserve context,
measurements and source history. Current-program resistance/current sessions require
an explicit full composition and draw/piece in their folder name. Supply current
limits are not inferred as annealing treatment setpoints.

Skipped-source accounting previously resolved the same cloud paths for every
retained record. It now resolves each candidate once. Additive annealing refreshes
retain manual phase points outside the imported subset. Successful updates print
ASCII-safe JSON while preserving Unicode in UTF-8 manifests, preventing a console
encoding error after a successful save.

Sidecar reconciliation now treats accepted-auto and manual-adjusted decisions with
identical final points as equivalent. Reopening an unchanged conflict preserves
the original project decision without recursively wrapping its history. The staged
database comparison identified and corrected 64 false annealing conflicts and 205
repeated TMA history wrappers. Original scientific decisions remain preserved;
genuine existing conflicts still require review.

The final follow-up run passed **722 tests in 202.83 seconds**, including **51
integrity regression cases**, with the frozen lock, offscreen Qt and isolated
settings/stores. No real project or instrument was used as test data. The staged
copy retained all 1,392 historical measurement curves and added 19 explicitly
identified electrical records and 56 TMA records, reaching 1,467. Three existing TMA
text summaries were recomputed; their numerical arrays and other fields remained
identical. Fabrication indexes retained every historical nonblank value and key,
adding 3 filtered pieces and 18 raw pieces. Saved manual overrides were retained.

The inventory also records 97 current-program sessions awaiting reliable sample
identity, including 32 with the shorthand Cu1Co1 1-5. TMA discovery found 147
sessions; completion gating and parser checks must remain visible in the update
manifest. Empty VSM placeholders do not replace embedded curves. DMA, FMR, manual
stress/strain, microscopy and existing VSM data remain preserved. EBSD, XRD,
ac-susceptibility, R-vs-T and VSM-isotherm folders are inventoried; this does not
claim their import into an unsupported Builder section. New readings from
fabrication videos remain a separate evidence-based review.
