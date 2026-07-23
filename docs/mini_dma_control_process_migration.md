# Mini DMA/TMA control-process migration

Status: production cutover on `codex/tma-control-process-production`, stacked on
`codex/hmp-usb-cadence-arbitration` at `21c7c11c` (PR 300). PR 298 has merged.
This branch must remain stacked on PR 300 until that branch merges; afterwards,
rebase onto current `main` and retarget this pull request. Do not merge or
rewrite PR 300 as part of this work.

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
2. A production child adapter constructs the existing Mini DMA controller only
   inside the spawned process. Scale acquisition, Tic and PSU objects, recipe
   clocks/state, immediate confirmation, and all run writers therefore remain
   together in the authoritative child.
3. The visible window captures immutable JSON configuration, releases any local
   device handles, starts the child, and thereafter sends session-scoped
   lifecycle commands. It renders coalesced immutable snapshots.
4. Parent-side scale, PSU, and Tic construction is fenced while the child owns
   the recipe. The child retains the existing cross-process Tic device lease.
5. The production child runs the existing recipe implementation unchanged, so
   Prague legacy-seek and Košice adaptive policies stay separate while sharing
   the isolated process and hardware infrastructure.
6. Disposable test windows default to the legacy in-process path. Tests must
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
  selection, command confirmation, and refusal of parent-side hardware access.
- Existing Prague and Košice controller suites must remain green; this migration
  does not change their physical assumptions or control laws.
- Packaging analysis must include the dynamically imported process kernel and
  production backend.

## Remaining live gate

No live hardware command is authorized by this implementation or its software
verification. A separately authorized run must begin with the repository
campaign preflight and ownership/safety checks. It remains the final evidence
for real driver enumeration, device timing, physical limits, emergency output
state, and end-to-end measurement files.
