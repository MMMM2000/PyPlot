2026-02-23 07:50
- Shared `Open in Origin` export now creates/updates graph titles through layer-scoped `label -s -n title "..."` plus object-API positioning, improving title visibility reliability across Origin 2026 builds.
- Shared Origin title export no longer uses `title.show` or root-level LabTalk fallbacks, preventing `TITLE.SHOW is illegal name` and worksheet-context `Math cannot be performed on Text column` errors.
- Shared Origin title export now uses `label -s -n title "..."` (plus an object-API/manual-label fallback) instead of `title -s`, matching Origin 2026 behavior where `title -s` can be parsed inconsistently from worksheet context.
- Shared Origin title commands are now executed strictly through the primary layer context and then re-applied after layer rescale, with title position computed from layer ranges so the title reliably renders at top-center in Origin 2026.
- Shared Origin title export no longer writes unsupported `title.just`/page-attach commands, preventing repeated `TITLE.JUST is illegal name` errors.
- Shared Origin graph creation now prefers the `line` template before `ORIGIN`/`scatter` fallbacks to reduce recurring template-side `LEGEND.SMARTPOS` warnings in affected Origin 2026 setups.
- Shared Origin dual-axis export again prefers `add_layer(4)` (`TopXRightY`) first so top/right axes stay linked to the primary layer scales; plain-layer fallback remains for runtimes where preset creation fails.
- Shared Origin export explicitly rescales layers after plotting so load/stress axes are not left in incorrect default ranges.
