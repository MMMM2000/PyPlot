# Worker Coordination Workflow

This workflow is for the PyPlot master coordination thread when multiple Codex workers, worktrees, subagents, or monitor automations are active at the same time.

The goal is to avoid losing completed work, avoid regressing accepted fixes, and make every integration decision based on branch state and evidence rather than chat memory.

## Roles

- The master thread owns the task ledger, integration branch, readiness decisions, combined verification, PR creation, and final merge decisions.
- Worker threads own focused implementation tasks on durable branches/worktrees.
- Subagents are temporary helpers for investigation, review, code archaeology, run inspection, diff audits, or test planning.
- Monitor automations watch active workers and wake the master thread when a worker is ready, blocked, stale, or unsafe.

## Choosing Worker, Subagent, Or Master

Use a worker thread/worktree when the task needs any of these:

- code edits that should become a commit,
- screenshots or GUI verification,
- a durable branch for later review or merging,
- long-running tests or hardware-adjacent work,
- live bench execution,
- a scoped `/goal`.

Use a subagent when the task is temporary and read-only:

- inspect a run folder,
- compare branches,
- find where a fix was introduced,
- audit a diff for regressions,
- summarize artifacts,
- propose tests or next experiments.

Use the master thread directly only for:

- task planning and delegation,
- read-only coordination checks,
- small integration glue after merging ready branches,
- updating the task ledger and workflow docs,
- final PR/merge preparation.

## Worker Task Ledger

Keep a single ledger for active and recently completed worker branches. The ledger can live in the master thread notes, a campaign folder, or a copied YAML file based on `docs/automation_templates/worker_task_ledger.yaml`.

Each task should record:

- task id and title,
- owner thread id or worker name,
- branch and worktree,
- base branch used,
- status,
- scope,
- changed files or likely conflict areas,
- behavioral guarantees,
- verification evidence,
- handoff link or pasted handoff text,
- integration decision,
- follow-up checks after merge.

Statuses:

- `planned`: task exists but no worker has started.
- `active`: worker is running or expected to continue.
- `ready`: worker reported a clean branch, commit, tests, risks, and evidence.
- `integrating`: master is merging or validating it.
- `integrated`: branch was merged and combined checks passed.
- `blocked`: worker cannot continue safely or productively.
- `deferred`: intentionally not integrated yet.
- `superseded`: replaced by a newer branch or task.

## Readiness Gate

A worker is ready only when its handoff includes:

- branch and commit,
- clean git status against its upstream,
- base branch used,
- behavioral guarantees it claims,
- focused tests/checks and results,
- screenshots or generated artifacts for visible UI/graph changes,
- hardware checks and final safety state for hardware work,
- changelog/docs status,
- risks, skips, and unverified behavior.

If any of these are missing, keep the task `active` or `blocked`; do not silently integrate it.

## Integration Flow

1. Update the ledger entry from the worker handoff.
2. Confirm the branch exists and fetch latest remotes.
3. Check whether the worker touched files overlapping accepted guarantees.
4. Merge one ready branch at a time into the integration branch.
5. Run focused combined checks for the touched area.
6. Re-run recent guarantee checks for overlapping behavior.
7. Update the ledger to `integrated`, `blocked`, or `deferred`.
8. Consolidate changelog fragments when preparing the final PR to `main`.

Accepted guarantees are part of the integration contract. If a later branch touches the same area, explicitly verify that those guarantees still hold.

## Monitor Automation Contract

A monitor should not do ordinary implementation work. Its job is to inspect, summarize, and steer.

Each monitor pass should:

- check active ledger entries,
- inspect worker handoffs and latest branch commits,
- detect approval stalls, context-compaction stalls, system errors, and stale processes,
- verify whether live hardware is safe before advising any continuation,
- remind workers to commit/push and send a complete handoff,
- notify the master when a worker becomes ready or blocked.

For hardware-adjacent workers, the monitor must not start hardware from the master thread unless the user explicitly authorizes it. It may only steer the worker and inspect evidence.

## Windows Worktree Rule

On this machine, new PyPlot worker threads should be created inside the PyPlot project with a new worktree and `no environment` selected. This avoids setup failures caused by non-ASCII user paths while still keeping implementation branches isolated.

Avoid creating worker threads in the generic Chats area for PyPlot tasks. If a task accidentally starts there but is already making progress, let it finish only if it is safe, then bring the branch back through the ledger.

## TMA Optimization Tasks

TMA live optimization tasks must start from a campaign manifest, not from chat memory or isolated artifacts. The campaign must identify the approved control source, sample, hardware channels, safety limits, run stages, report outputs, and success metrics.

Before a live run, the worker must run:

```powershell
uv run python scripts/mini_dma_campaign_check.py <campaign.yaml>
```

After each run, the worker should write or refresh:

```powershell
uv run python scripts/mini_dma_run_quality.py <run-folder> --write
uv run python scripts/mini_dma_report.py <campaign.yaml>
```

During live optimization, each run handoff should also include a phone-readable core plot image, generated immediately after that run rather than only in the final report. The image should use the standard two-panel view: stress vs time on the left, strain vs measured current on the right, with current-hold periods highlighted and annotations for the run folder, ramp settings, stop reason, and key stress-error metrics.

The worker handoff must include the run folder, stop reason, control logic fingerprint, source commit, stress-error metrics, current compliance summary, current-hold behavior, per-run core plot artifact path, and final HMP channel state.
