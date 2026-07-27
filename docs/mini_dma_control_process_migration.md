# Mini DMA/TMA control-process migration

Status: production cutover on `codex/tma-control-process-production` (PR 301).
PRs 298, 300, and 302 are merged. Current `main` was merged into this branch on
2026-07-27; PR 301 must now target `main`.

## Evidence from the previous architecture

- `AutomationControlLoop` ran recipe ticks on a Python thread, so ordinary Qt
  repaint work did not directly set tick cadence.
- `MiniDmaAutomationController` delegated every recipe action back to
  `MainWindow`. Scale state, Prague/Košice force policy, Tic and PSU commands,
  recipe state, logging, and UI publication therefore remained in the GUI
  process.
- `ScaleWorker`, `TicCommandDispatcher`, `PowerSupplyController`,
  `SharedBrokerSupplyController`, `AsyncRunLogWriter`, and session writers were
  constructed or retained by `MainWindow`.
- Pause, resume, stop, emergency stop, and close combined controller, hardware,
  logging, and widget transitions. A busy GUI process could still delay or
  starve the authoritative controller despite the worker thread.

## Implemented production boundary

1. The dependency-light process kernel owns immutable commands, snapshots and
   events, generation checks, bounded channels, heartbeat/crash handling, and
   an out-of-band emergency path. It imports no Qt, serial, Tic, or PSU code.
2. The visible UI retains operator-facing preparation: stopped-run handling,
   previous-run and first-overheating decisions, hardware preflight, continuity
   preparation, the mounted-length prompt, and responsive manual setup controls.
   Manual Tic jogging uses bounded target-position steps directly from the UI
   while no isolated recipe owns the hardware; it is not routed through the
   recipe child.
3. Immediately before the run, the visible UI freezes immutable JSON
   configuration, stops its acquisition workers, closes its PSU and Tic
   objects, explicitly releases the Tic ownership lease, and starts the child.
   A failed or non-quiescent release aborts startup.
4. A production child adapter reconstructs the existing Mini DMA runtime only
   inside the spawned process, reacquires the required hardware, verifies the
   handoff, and then starts the recipe. Scale acquisition, Tic and PSU objects,
   recipe clocks/state, immediate confirmation, and all run writers remain
   together in the authoritative child.
5. During the run the visible UI sends only session-scoped lifecycle or
   explicitly permitted runtime-edit commands and renders coalesced immutable
   snapshots. UI repaint or event-loop stalls cannot clock the child recipe.
   Emergency Stop uses the child's out-of-band event even when the ordinary
   command queue is full or another lifecycle command is awaiting confirmation.
6. Parent-side scale, PSU, and Tic construction is fenced while the child owns
   the recipe. Manual Actions and Hardware controls are disabled until the
   child reports completion, stop, emergency, or fault. The child retains the
   existing cross-process Tic device lease.
7. The production child runs the existing recipe implementation unchanged, so
   Prague legacy-seek and Košice adaptive policies stay separate while sharing
   the isolated process and hardware infrastructure.
8. Disposable test windows default to the legacy in-process path. Tests must
   explicitly opt into isolation with a fake supervisor, preventing accidental
   serial access during software verification.

Normal persisted app launches default to the isolated production path. The
in-process path remains as an explicit constructor seam for deterministic tests
and for the child-host adapter.

## Software gates

- Spawned-process fake tests must demonstrate cadence during deliberate UI
  blocking, exclusive backend construction in the child PID, bounded queues,
  snapshot coalescing, stale-generation rejection, and emergency delivery.
- Lifecycle coverage must include start, pause, resume, stop, emergency,
  parent-heartbeat loss, child fault, command saturation, and shutdown.
- UI adapter tests must prove immutable configuration hand-off, policy
  selection, unchanged pre-run ordering, explicit hardware and lease release
  before child spawn, command confirmation, and refusal of parent-side hardware
  access.
- Existing Prague and Košice controller suites must remain green; this migration
  does not change their physical assumptions or control laws.
- Source-based launches are the supported deployment path for this application;
  building a PyInstaller executable is not a release gate.

## Remaining live gate

No live hardware command is authorized by this implementation or its software
verification. A separately authorized run must begin with the repository
campaign preflight and ownership/safety checks. It remains the final evidence
for real driver enumeration, device timing, physical limits, emergency output
state, and end-to-end measurement files.
