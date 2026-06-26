# AGENT GUIDELINES

## Core Operating Model
- Treat `main` as the stable runnable baseline.
- Use focused worker threads/worktrees for implementation.
- Use the master coordination thread for planning, delegation, integration, combined verification, and PR/merge decisions.
- Do not use the master thread for ordinary feature coding unless the user explicitly asks for it or the change is a tiny integration-only fix.
- Worker threads own focused tasks; the master thread owns deciding when ready branches are integrated.

## Master Coordination Workflow
- When the user asks for new work, decide whether it belongs in an existing worker, a new worker, or this master thread.
- Track active, ready, blocked, deferred, and integrated worker tasks in a single ledger. Use `docs/worker_coordination_workflow.md` and `docs/automation_templates/worker_task_ledger.yaml` as the default workflow/template when several workers or substantial deferred branches exist.
- Use worker `/goal`s for substantial, risky, long-running, or hardware-adjacent tasks. The user does not need to set these manually.
- A worker branch is ready for integration only after it reports:
  - branch and commit,
  - clean git status,
  - behavioral guarantees it now claims to preserve,
  - focused tests/checks and results,
  - visual screenshots for visible UI or graph changes,
  - changelog/docs status,
  - known risks or unverified behavior.
- Merge ready branches into an integration branch one at a time.
- After each merge, run relevant combined checks, including recently accepted behavioral guarantees that touch overlapping areas. If a merge exposes integration failures, fix only small integration glue here or delegate the fix back out.
- Keep integration branches named clearly, for example `codex/integration-mini-dma-ready-review`.
- Create and merge final PRs from the master thread by default. Use a separate PR-finalization worker only if PR cleanup becomes substantial.
- Check active worker status opportunistically when the user prompts the master thread.
- For TMA optimization campaigns, the master thread owns creating or approving the campaign manifest before delegating live hardware work.
- Monitor automations should inspect and steer workers, not silently perform implementation or start hardware from the master thread.

## Worker Thread Workflow
- Work in a dedicated branch/worktree.
- Before coding, fetch and inspect branch/upstream state.
- Keep scope narrow and aligned with the delegated task.
- Do not run hardware commands unless explicitly authorized in that worker task.
- For UI or graph work, verify with screenshots or generated-output evidence from the worker branch.
- Commit and push coherent completed work after verification so the master thread can integrate from a durable branch instead of an uncommitted patch.
- For hardware-adjacent work, commit and push the relevant code/tooling before live execution whenever practical, so run metadata can point to a clean source state.
- If useful diagnostic/tooling changes are made while a task later becomes blocked, commit and push those changes when safe, but mark the handoff as blocked/not ready for integration.
- Do not merge to `main` or create final PRs from a worker unless the master thread explicitly delegates that responsibility.
- Report completion with branch, commit, tests/checks, screenshot paths when relevant, and integration notes.
- When ready or blocked, send a concise completion handoff back to the master coordination thread.
- If blocked, report the blocker, current branch, git status, and what was already verified.
- For TMA optimization workers, do not start live hardware from chat memory or isolated artifacts. Start from a campaign manifest, run `scripts/mini_dma_campaign_check.py`, and report the checker result before live execution.

## Delegation Strategy
- Choose the split that gives the cleanest reasoning, least conflict, and fastest useful feedback; do not default to either one worker for everything or one worker per issue.
- Keep tightly coupled issues in one worker when they share runtime state, safety logic, or the same focused verification loop.
- Split work across multiple workers when failure modes are separable, can be verified independently, or can progress in parallel without constant conflict.
- Use worker worktrees for durable implementation that needs commits, screenshots, GUI runs, branch review, or later merging.
- Use subagents for temporary investigation, code archaeology, review, test planning, diff audits, or parallel diagnosis inside a master or worker thread.
- Avoid endlessly appending scope to a running worker. Once a worker has a coherent batch, let it finish, integrate it, and start the next cluster from the updated integration branch.
- On this Windows machine, create PyPlot worker threads inside the PyPlot project with a new worktree and `no environment` selected. Avoid generic Chats/local-environment workers for PyPlot implementation unless the user explicitly asks for that mode.
- Name worker threads immediately with a short subsystem-first title that matches the task and branch/ledger entry, for example `TMA 12/2 optimization`, `AC logger broker autostart`, or `Thermo sensor MLX90614 bring-up`.
- Avoid leaving worker titles as generic prompt fragments such as `You are a focused worker...`, `Continue...`, or `Fix issue`; rename them as soon as the worker is created or when the real task becomes clear.
- Keep active worker threads pinned while they own unfinished work, pending integration, live validation, or deferred branch decisions. Unpin a worker only after its changes have been integrated here, explicitly rejected/deferred in the ledger, or the branch is no longer needed.

## Integration Regression Ledger
- Treat accepted worker behavior as part of the integration contract, not just the code diff.
- When a worker branch is accepted, record the user-visible behaviors it proved and the evidence that verified them, such as tests, screenshots, logs, or synthetic data.
- Examples of useful guarantees: "setup graph is not cropped", "Task label stays stable during stress ramp", "Microwire 10/1 -> 10/4 does not crash", and "equivalent unit labels preserve significant zeros".
- New worker branches should start from the latest integration branch whenever possible. If they start elsewhere, explicitly compare overlapping files and preserve accepted guarantees during forward-porting.
- Before bringing worker changes into the master/integration branch, check whether touched files overlap with recent guarantees and plan verification for those guarantees.
- After integration, rerun the relevant guarantee checks from recently accepted branches, especially for TMA UI, graph layout, task summaries, and hardware-control state.

## Autonomous Operability
- Build tools and workflows so Codex can operate them safely and repeatably without manual UI intervention.
- Prefer explicit CLI entrypoints, JSON plans, dry-run modes, fake-driver modes, and machine-readable status/artifact outputs.
- Long-running or hardware-adjacent workflows should write progress, metadata, logs, stop reasons, and artifact paths clearly enough that another thread can inspect and resume or diagnose them.
- Avoid hidden modal dialogs in automation paths. Provide noninteractive overrides for bench/agent workflows.
- Make safety state observable: active process, hardware ownership, channel leases, output state, current recipe step, stop reason, and artifact paths.
- Keep manual UI workflows pleasant, but do not make automation depend on clicking through the UI.

## TMA Optimization Campaigns
- Treat TMA optimization as a repeatable campaign, not an ad hoc run.
- Make the optimization objective explicit before live work: minimize stress/load fluctuation, minimize stress error, recover quickly after transformation-driven stress changes, preserve useful strain-current curves, and quantify the measurement-time versus precision tradeoff.
- Keep raw run history and reports in `G:\My Drive\1 Projects\Praha\mini DMA\automation_history`; keep reusable templates, recipes, schemas, scripts, and docs in the repo.
- Every optimization campaign should have a `campaign.yaml` based on `docs/automation_templates/mini_dma_campaign.yaml`.
- Before live optimization hardware, run:
  - `uv run python scripts/mini_dma_campaign_check.py <campaign.yaml>`
- The campaign manifest must define sample identity, length, diameter source, approved control source, hardware channels, voltage/current limits, safety rails, run stages, and reporting outputs.
- Optimization workers must start from the latest approved control logic named by the campaign, normally latest `main` or the current TMA integration branch. Do not use a random stale worker branch just because it has local artifacts.
- If the campaign checker says the branch is dirty, behind the approved base, missing control source, or missing report paths, stop and ask the master thread to fix the campaign or integration state before running hardware.
- Do not tune permanent TMA control logic to one sample with hard-coded magic values. Prefer adaptive or physically derived rules based on diameter, length, stiffness/calibration, noise, motor step size, stress/load trend, current ramp rate, and measured compliance. Hard caps are acceptable for safety or campaign-local experiments, but they must be clearly labeled as such.
- After campaign runs, generate the standard report with:
  - `uv run python scripts/mini_dma_run_quality.py <run-folder> --write`
  - `uv run python scripts/mini_dma_report.py <campaign.yaml>`
- Standard reports must include stress vs time, strain vs measured current, and current-hold highlighting. Exploratory plots may be added, but do not replace the core plot pair.
- During live optimization, generate the same core plot pair after every hardware run, not only at the end of the campaign. Each per-run update should include a phone-readable image artifact with stress vs time on the left, strain vs measured current on the right, current-hold periods highlighted, and enough annotation to identify the run folder, stop reason, ramp settings, and key stress-error metrics.
- Include relevant normal non-optimization runs as reference evidence using cached `run_quality.json` summaries when available, but classify and exclude setup failures, wire breaks, very short runs, and too-early bring-up attempts with explicit reasons.
- For temperature/current-ramp optimization, encode fixed ramp speeds and dynamic-ramp candidates as explicit campaign stages so precision/time comparisons are repeatable.
- Slower current ramps must justify their extra time with measurable precision or curve-quality gains; for example, check whether `0.2 mA/s` is actually worth the much longer measurement compared with `0.6` or `0.8 mA/s`.

## Environment
- Use `uv` for project Python commands and environment sync by default.
- Match the interpreter to `pyproject.toml` before creating or reusing `.venv`. This repo currently requires Python 3.14 (`>=3.14,<3.15`); do not fall back to Python 3.13.
- If `py -0p` does not list a Python 3.14 interpreter on Windows, check `%LOCALAPPDATA%\Programs\Python\Python314\python.exe`; if neither is available, stop environment setup and report that Python 3.14 must be installed or registered before installing the project.
- Prefer `.venv` created by `uv sync --extra test --python 3.14`.
- State the interpreter reported by `uv run python --version` after dependency/setup work.
- Treat `.venv` as disposable generated state. If the project now requires a newer Python version, replace the old `.venv` instead of asking the user to clean it up.
- On Windows accounts with non-ASCII user paths, expect tool temp/cache issues. Prefer safe temp/cache locations such as:
  - `TEMP=artifacts/tool-temp`
  - `TMP=artifacts/tool-temp`
  - `UV_CACHE_DIR=artifacts/uv-cache` or `C:\tmp\uv-cache`
  - `PIP_CACHE_DIR=artifacts/pip-cache`
- If `uv` is unavailable, first try to fix `uv` availability. Use the pip compatibility fallback only when `uv` cannot reasonably be made available.

## Dependencies
- Edit `pyproject.toml` first, then regenerate `uv.lock` with `uv lock`.
- Keep `pyproject.toml`, `uv.lock`, `requirements.txt`, and generated lock/export headers aligned with the Python version used to compile them.
- Export pip compatibility requirements from the lock with `uv export --format requirements.txt --no-hashes --no-emit-project --output-file requirements.txt`.
- If Windows-only dependencies change, also sync `requirements-win.txt`.
- If a clean uv environment exposes a direct import that was previously only present by accident, add it to `pyproject.toml` and regenerate `uv.lock` plus compatibility exports.
- After dependency/runtime changes, sanity-check imports for the relevant stack. At minimum for core runtime changes check PyQt6, matplotlib, numpy, pandas, scipy, and plotly; also check Origin/PDF packages or `cv2` when those dependencies are declared or touched.

## Git Sync
- Before substantive work, run `git fetch --all --prune`.
- If the current branch tracks an upstream and can fast-forward cleanly, update with `git pull --ff-only`.
- Do not force sync, reset, or discard changes unless explicitly requested or clearly approved.
- If there are local changes, no upstream branch, or the update would require merge/rebase, state the situation and continue only when the user intentionally created or selected the current branch.
- In linked worktrees, Git may need permission to update shared `.git/worktrees` metadata.

## Changelog
- Keep `CHANGELOG.md` as the canonical release history on `main`.
- In feature branches/worktrees, add changelog fragments under `changelog.d/` instead of editing `CHANGELOG.md` directly.
- Fragment format: start with a UTC timestamp `YYYY-MM-DD HH:MM`, then a concise bullet list of user-facing changes.
- Call out migrations, runtime requirement changes, dependency upgrades, and notable compatibility fixes explicitly.
- The master/integration thread owns changelog consolidation by default because it knows which worker branches are included.
- When preparing or merging an integration branch to `main`, consolidate relevant `changelog.d/` fragments into `CHANGELOG.md` and remove the consumed fragments.
- If many old fragments exist, do a dedicated changelog cleanup pass rather than mixing broad cleanup into unrelated feature work.

## Verification
- Verify changed behavior before reporting work as done.
- Scale verification to risk: focused checks in worker branches, combined checks in integration branches.
- For visible UI or graph changes, provide screenshot or generated-output evidence.
- For dependency, runtime, or app-entry changes, run a startup smoke check such as `uv run python launcher.py --help`.
- Report what passed and what was intentionally skipped.
- Never verify directly against the user's real `.pypj` or `.pydpj` files. Use disposable copies or isolated synthetic test data.
- If tests need Microwire Data Builder storage, isolate app-data stores with a temporary storage root; do not write to the user's real `.microwire_data_builder` data.
- For Qt tests in headless shells, set `QT_QPA_PLATFORM=offscreen`; for Matplotlib-heavy tests, set `MPLBACKEND=Agg`.
- If pytest cache/temp creation causes Windows access-denied errors, rerun with a fresh workspace temp directory and consider `-p no:cacheprovider`.

## Hardware Safety
- Hardware ownership must be explicit.
- Do not run hardware commands from worker threads unless the delegated task authorizes it.
- State channel assumptions before hardware work.
- Prefer software tests/fake drivers before live hardware.
- For TMA/HMP work, avoid starting duplicate controllers. Confirm active processes/channels before long runs.
- When stopping after hardware errors, explicitly verify safe output state where possible.

## Diagnostics
- When investigating crashes or errors, check `logs/message_log.txt` and other files under `logs/` first, then summarize relevant traces.
- For path-default or cache-home problems, inspect process-wide environment changes as well as immediate UI code; `HOME`, `USERPROFILE`, and tool-specific cache variables can affect unrelated dialogs.
- For UI crashes, test real signal paths, not only direct method calls: typing, completer selection, partial text, and fallback data loading.

## Origin Integration
- Consult the OriginLab Python docs when implementing or changing Origin-related behavior:
  https://docs.originlab.com/originpro/index.html
- Treat Origin validation as Windows- and installation-dependent. If Origin or its automation runtime is unavailable, run the non-Origin checks and state that Origin verification was skipped.

## Artifacts And Cleanup
- Store temporary visual-check, pytest, pip, and diagnostic outputs under ignored workspace folders, preferably `artifacts/`.
- Cleanup is best effort on Windows. If a generated temp directory cannot be removed because of permissions or a live handle, summarize the path and what cleanup was attempted.
- Do not remove user data, real project files, or persistent app-data caches unless the user explicitly asks for that cleanup and the target path has been verified.

## Docs
- When changing user-facing PyPlot or plugin behavior, workflows, project formats, or conventions, check the relevant docs such as `docs/pyplot.md` and update them when the behavior changes.
- Pure dependency bumps, internal test fixes, or narrow compatibility guards usually do not need docs unless they change setup requirements or user-visible behavior.
