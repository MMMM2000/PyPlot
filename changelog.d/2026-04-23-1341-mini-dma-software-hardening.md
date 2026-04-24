2026-04-23 13:41 UTC

- Mini DMA Logger now keeps the last confirmed stage position separate from the commanded target so strain, stress, and recorded points do not jump ahead of real motion after a move command.
- Hsw distribution seeking now refuses to act on stale or missing balance readings for load- and stress-based control instead of nudging the stage on old force data.
- Mini DMA session metadata now preserves the original creation timestamp across JSON sidecar rewrites during a run.
- Mini DMA now supports active hardware auto-detection for the G&G scale, the serial supply, and the Pololu Tic controller, plus a real G&G remote tare action alongside the existing software tare offset.
- Mini DMA naming now mirrors the other microwire loggers more closely by keeping human-readable microwire tokens like `156/2` in the sample name while using file-safe tokens like `156_2` in the output filename.
- Mini DMA's settings panel now prevents mouse-wheel scrolling from silently changing spin-box and drop-down values, removes horizontal scrolling, and exposes tare actions in the manual setup controls.
- Added a Mini DMA measurement plan covering the copper-wire first test, the intended isostress current-sweep workflow, saved recipe files, and later dynamic recipes.
- Microwire Data Builder video overrides now tolerate minimal video tables that are missing derived video-length columns.
- Added dedicated Mini DMA regression tests for confirmed-position tracking, stale-scale safety, session metadata stability, hardware auto-detection, remote tare wiring, and naming behavior, and expanded import coverage to include the Mini DMA module.
- Added `scipy==1.17.1` as a runtime dependency so `microwire_eda` imports and the related launcher test path work in a fresh project environment.
