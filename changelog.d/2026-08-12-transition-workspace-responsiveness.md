- Keep the Builder responsive while transition source paths are checked, avoid
  rebuilding clean Annealing/VSM/TMA overview tabs, and precompute VSM cycle
  splitting in the existing background project loader.
- Preserve visible loading feedback during cold loads and keep sorted overview
  rows aligned with their asynchronous source-availability results.
- Render heating and cooling as separate red and blue traces in the shared
  transition reviewer, and correctly reload labelled Košice Current Annealing
  `.dat` files instead of mistaking cycle numbers for current values.
- Replaced the separate legacy VSM transition window with the same compact PyQtGraph queue and Auto/Manual/Not observed controls used for current annealing and TMA; VSM saves now write a portable sidecar and mirror into Builder project state.
- Kept review cycles tied to measured traces so a partially reviewed first cycle cannot make later cycles disappear or display duplicate data, and Save now advances through cycles/targets before the next run.
- Added persistent green, amber, and red freshness indicators beside each section's Refresh controls, keeping the main and transition tab bars compact while preserving explanatory tooltips.
- Kept freshness state stable across tab switches, cleared false load failures after a
  successful retry, and derived Assemble/Compare status from their actual cached views.
- Deferred oversized packaged DMA and VSM graph payloads, kept their full graphs available
  on demand, and prevented Compare from synchronously decoding deferred source payloads.
- Debounced and bounded Videos table autosizing so background sizing cannot freeze a later
  VSM or TMA tab switch.
- Label VSM transition targets by their physical cycle only, without presenting the
  filename temperature token as a constant scan temperature.
- Preserve legacy VSM manual reviews across corrected sample metadata by matching a
  unique unchanged source path, and plot parser-defined heating/cooling branches
  without cross-branch connector lines.
- Keep large packaged project saves responsive with visible progress, flush transition
  review updates before returning to the user, and add explicit Prague/Košice filtering
  while separating laboratory provenance from data-file availability.
- Make Up and Down arrow navigation symmetric in the shared transition-review
  tree by moving directly between review cycles/targets and skipping grouping rows;
  selecting a sample or run header now visibly transfers to its first review unit.
- Replace lazy Open/Not opened labels with shared scientific review states for
  CA, VSM, and TMA, aggregate reviewed progress by run and sample, and add a
  compact All/Unreviewed/Reviewed/Excluded queue filter with more sidebar width
  reserved for sample, run, and cycle names; keep the Review column narrow by
  default while allowing its divider to be resized manually.
- Detect packaged Builder projects from their ZIP signature before applying the
  legacy JSON size limit, allowing large lazy-loaded `.pydpj` packages to open.
- Prevent the completed transition editor from being shown again after
  Save-and-next advances to the next visible cycle or run.
- Add a reversible run-level Mark for archive decision shared by CA, VSM, and
  TMA reviews; it records the request in the portable sidecar and excludes the
  run from Builder analysis without moving source data. Render small measured-
  point symbols over transition lines, decimated automatically for dense traces.
- Populate the shared review queue and editors from historical Builder project
  reviews when no portable sidecar exists, including legacy TMA target IDs, so
  Reviewed filtering and saved transition values remain available during the
  gradual sidecar backfill.
