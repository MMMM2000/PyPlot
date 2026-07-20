# Mini DMA/TMA control-process migration

Status: incremental stacked work based on `codex/mini-dma-transform-disturbance-invariant`
at `ee10c238` (PR 298, including the motor-confirmation fixes and the PR 299
Košice import). This branch must remain based on PR 298 until PR 298 is merged.
After that merge, rebase this branch onto `main` and retarget its pull request;
do not merge or rewrite PR 298 as part of this work.

## Evidence from the current architecture

- `AutomationControlLoop` runs recipe ticks on a Python thread, so tick cadence
  no longer directly depends on Qt repaint cadence.
- `MiniDmaAutomationController` still owns only dispatch. Its host is
  `MainWindow`, and every recipe action calls back into that window for scale
  state, force policy, Tic commands, PSU commands, recipe state, logging, and
  UI publication.
- `ScaleWorker`, `TicCommandDispatcher`, `PowerSupplyController`,
  `SharedBrokerSupplyController`, `AsyncRunLogWriter`, and the session writers
  are all constructed or retained by `MainWindow`. The GUI process therefore
  remains the authoritative hardware and run-state owner.
- Pause, resume, stop, emergency stop, and close currently combine controller,
  hardware, logging, and widget transitions in `MainWindow` methods. Several
  worker paths must marshal back to the Qt thread before completing.
- `MiniDmaControlConfig`, `MiniDmaRunMetadataSnapshot`, and frozen Tic settings
  already provide useful immutable hand-off points. Prague and Košice policy
  selection is already explicit through `ForceControlProfile`; it should not be
  collapsed during the infrastructure migration.
- Existing tests prove thread independence, frozen settings, Prague/Košice
  policy separation, immediate Tic target confirmation, asynchronous logging,
  and simulator behavior. They do not yet prove OS-process isolation or
  exclusive hardware ownership.

## Target boundary

The control child process will be the only process allowed to construct or use
the active scale, Tic, and PSU adapters. It will own recipe clocks and state,
live readback confirmation, safety decisions, and authoritative run files. The
Qt process will build immutable configuration, send sequenced operator
commands, and render immutable/downsampled snapshots and events.

Every IPC message carries a session identity and generation. Commands are
bounded and reject overload instead of silently growing. Emergency and shutdown
signals have out-of-band paths so command saturation cannot hide them. Snapshot
delivery is a one-item latest-value channel; event delivery is bounded and
reports dropped-event counts. A non-Qt heartbeat thread represents parent
process liveness, so a blocked Qt event loop does not falsely trip the control
process. Loss of the parent heartbeat, an unhandled control exception, or an
explicit emergency request drives the child through the backend emergency-safe
path.

## Incremental migration

1. **Process/IPC kernel (this branch).** Add a dependency-light, spawn-safe
   process supervisor and runtime with immutable commands/snapshots/events,
   generation checks, bounded IPC, heartbeat/crash behavior, and a deterministic
   simulated backend. Do not connect it to production devices yet.
2. **Authoritative logging seam.** Move run-directory allocation and the
   measurement/control-trace writers behind a process-owned run-log adapter.
   Preserve the existing generation-safe late-write accounting and metadata
   finalization invariants. The UI consumes log-status events only.
3. **Scale ownership.** Move serial scale construction, acquisition, filtering,
   freshness, and safety-limit evaluation into the child. Publish downsampled
   immutable scale/readback snapshots. Keep all fake-driver coverage offline.
4. **Tic and PSU ownership.** Construct the Tic dispatcher/controller and direct
   or broker-backed PSU adapter only inside the child. Keep immediate target
   status/readback confirmation in the child. Enforce one active hardware lease
   per process generation and fail closed on duplicate ownership.
5. **Recipe/policy cutover.** Move the existing recipe state machine and frozen
   `MiniDmaControlConfig` into the child without changing policy behavior.
   Retain distinct Prague legacy-seek and Košice disturbance-aware force
   controllers over the shared process-owned hardware interfaces.
6. **Qt adapter and removal.** Replace `MainWindow` control calls with a thin IPC
   adapter, coalesce visual updates, and remove the old in-process control loop
   only after parity tests cover start, pause, resume, stop, emergency, crash,
   reconnect, stale generations, and both control policies.

Each step should be independently mergeable and must keep the existing
in-process path as the production default until the corresponding hardware and
logging parity tests are complete. No live-hardware validation is authorized by
this plan.

## Gates before production cutover

- Spawned-process fake tests demonstrate cadence during deliberate UI-thread
  blocking, exclusive backend construction in the child PID, bounded queues,
  snapshot coalescing, stale-generation rejection, and emergency delivery.
- Deterministic simulator matrices demonstrate unchanged Prague and Košice
  decisions from identical recorded inputs.
- Fault injection covers scale/Tic/PSU exceptions, writer failure, parent death,
  child death, command saturation, and shutdown timeouts, with the expected
  final motor/PSU state recorded.
- Packaging tests confirm the spawn entry point and hidden imports in the frozen
  Windows application.
- Live hardware work, if later authorized, begins only after the repository's
  campaign preflight and ownership/safety checks.
