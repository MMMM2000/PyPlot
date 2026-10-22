# Research inputs and reproducible fixtures

Routine tests use the small text fixtures in `tests/fixtures/` and invented inputs
written by `tests/synthetic_data.py` into isolated pytest directories. They need
no Drive connection, credentials, research archive, or persistent app storage.
Keep new scientific parser edge cases as minimal synthetic examples with explicit
expected values. Do not add real measurements, databases, videos, or fabrication
workbooks to Git.

For scientific end-to-end checks, locate the selected source through connected
Google Drive and record its file ID/URL, name, MIME type, revision/modified time,
size, and SHA-256 of the downloaded bytes. Materialize only the selected files in
a task-specific ignored `artifacts/` directory. Run against disposable copies with
isolated Builder storage, Qt settings, Matplotlib caches, and temp directories;
record commands, source commit, results, and unchanged input hashes. Preserve the
raw/shared source. A Drive connector is not a reproducible CI fixture source.
Record unresolved provenance rather than equating files by name.

Input dialogs use the remembered directory or Downloads. Logger source resolution
still supports recorded and project-relative sources; an unavailable source must
be selected explicitly instead of silently finding a repository research copy.
The serial emulator has a small synthetic streaming fallback when optional inputs
are absent. It does not authorize hardware activity.

Manual tools require deliberate inputs:

- `scripts/benchmark_builder_review_smoothness.py --mini-dma-run <disposable-run>`
  expects `measurement.csv` and `metadata.json` in the selected directory.
- `scripts/verify_origin_plugins.py --data-root <disposable-input-root> --plugin ...`
  expects the selected plugin's former subdirectory layout (for example
  `DMA/*.txt`, `VSM_data/vsm_isotherms/*`, or `current_annealing/*.txt`). Select only
  the relevant plugins and inputs. Its existing Origin automation requires its own
  authorized local acceptance task.
- The shape-memory visual check requires explicit input paths; no inputs returns
  a diagnostic before opening an application.

## Recovery of the former repository copies

The removal baseline is commit `b4407621704f24a94078d51b8ec3cc8382365f16`.
The feature branch removes 958 tracked files, totaling **393,625,810 Git blob
bytes** (375.4 MiB). This includes two fabrication videos, measurement exports,
workbooks, and small pointer files. This count describes tracked blobs, not Windows
checkout bytes or reclaimed disk space.

`sample-data-inventory.csv` records each original path, mode, blob ID, size, and
source commit. Its SHA-256 is
`0fde7605bf204b6be86e93e8a7a487cf371001db68dae1b580db862dc9c0fcb9`
(canonical LF text). The original pre-removal CSV is retained in task artifacts;
its CRLF-byte SHA-256 is
`e602fabd07b50e4b2d72f4829d5ed24e7936f555e3b7c64035caa2f376ab2ccf`.
To recover into a new disposable directory, use
`git archive <source-commit> sample_data -o <outside-working-tree>/samples.zip`;
or extract a particular blob by its recorded ID with `git cat-file blob <blob>`.
Do not restore over existing research work. The primary checkout and all original
Git history remain intact; this change does not retract existing clones or erase
historical blobs.

The inventory records repository provenance only. It does not certify publication
rights, physical sample identity, completeness of a Drive archive, or backup of
every historical export. Those decisions remain with the research owner before
any later history migration. No Drive file was changed or removed.
