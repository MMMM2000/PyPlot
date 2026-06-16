# PyPlot Stabilization Campaign - 2026-06-16

This is a coordination note for the overnight stabilization pass requested by the user. Do not treat this as a release PR. The user wants to test the resulting branch manually before any final PR or merge to `main`.

## Scope

- No final PR.
- No merge to `main`.
- No live hardware, serial-port, HMP, LCR, Tic, or camera commands from this campaign unless explicitly authorized later.
- Current branch at the time of this note: `codex/ac-susceptibility-ui-stabilization`, based on commit `03f766c` from `codex/integration-mini-dma-ready-review`.

## Agent Work

- `019ed1d4-8f4a-7fb0-95a7-0284afe0168a`: read-only Mini DMA run forensics and controller audit.
- `019ed1d4-a40e-7703-a4be-e906e1d5ec2f`: Mini DMA run-quality, core-plot, and trace-replay tooling.
- `019ed1d4-ba94-72b3-9643-917e1a464334`: partial Mini DMA Builder project cache/autofill responsiveness.
- `019ed1d4-cbee-7700-b5a7-0c6f4760213e`: shared HMP broker diagnostics and stale-lease retry handling.
- `019ed1d4-e815-7541-96b6-796d2013b7bf`: partial AC susceptibility UI cleanup.
- `019ed1d4-fd04-78b0-b43f-e469a91c5fc1`: read-only elastocaloric recipe design.

The multi-agent workspace was not isolated as expected, so the resulting patch queue was stabilized as one branch instead of separate worker branches.

## Verification So Far

- `python -m py_compile` passed for touched logger/tooling modules.
- `git diff --check` passed with only expected Windows CRLF warnings.
- Mini DMA run-quality/core-plot/trace-replay focused tests: `12 passed`.
- Shared HMP broker/setup focused tests: `17 passed`.
- AC susceptibility logger tests: `124 passed`.
- Combined Mini DMA tooling and shared HMP focused tests: `29 passed`.
- Mini DMA sample/autofill focused tests: `11 passed`.
- Mini DMA shared-broker focused tests: `12 passed`.
- Current Annealing shared-broker focused tests: `26 passed`.
- Iso-current worker behavior slice from `e54955f`: `8 passed` on this branch; the branch content was already present, so the old worker commit was not cherry-picked over the newer integration base.
- Mini DMA wire-break/open-circuit focused slice: `13 passed`.
- Mini DMA ordinary-seek regression slice after predictive-control scoping: `9 passed`.
- Mini DMA adaptive current-sweep seek/control slice after predictive-control scoping: `10 passed`.
- Mini DMA IR cleanup regression: `1 passed`.
- Current Annealing fabrication-load UI responsiveness regression: `1 passed`.
- Wide stabilization suite (`tests/test_mini_dma_logger.py`, `tests/test_ac_susceptibility_logger.py`, `tests/test_shared_power_supply_broker.py`, `tests/test_shared_power_supply_setup_ui.py`, `tests/test_current_annealing_logger.py`, `tests/test_mini_dma_run_quality.py`, `tests/test_mini_dma_run_core_plot.py`, `tests/test_mini_dma_campaign.py`, `tests/test_mini_dma_automation_index.py`): `806 passed`.
- Shared HMP broker diagnostic classification tests: `17 passed` for `tests/test_shared_power_supply_broker.py`.
- Mini DMA shared-broker stale-lease retry focused slice: `3 passed`.
- Mini DMA Builder project sample-cache/stale-result/cancel focused slice: `8 passed`.
- Mini DMA elastocaloric recipe dropdown/build/JSON round-trip focused slice: `3 passed`.
- AC worker-failure/run-status UI slice: `7 passed`.
- AC sweep finish/stop status-summary UI slice: `5 passed`.
- AC output-status run-status-path UI slice: `4 passed`.
- AC/shared-HMP broker diagnostic slice: `8 passed`; full AC plus shared broker files: `142 passed`.
- Mini DMA sample naming/project-cache/completer focused slice: `19 passed`.
- Mini DMA run-quality/core-plot CLI focused slice: `11 passed`.
- AC sweep-start failure cleanup slice: `6 passed`; full AC plus shared-broker files after cleanup: `149 passed`.

## Artifacts

- AC UI offscreen screenshot: `artifacts/ac_ui_screenshot/ac_logger_workflow_panel.png`.
- Mini DMA elastocaloric recipe offscreen screenshot: `artifacts/elastocaloric_ui_screenshot/mini_dma_elastocaloric_recipe.png`.

Offscreen Qt font fallback renders text as boxes on this machine, but layout geometry is visible.

## Important Findings

- Latest inspected Mini DMA run `Ni50Fe25Ga25 3_1 iso-stress_run02` stopped as `wire_break_or_contact_loss` after voltage limit and current collapse: set current about `25.4 mA`, measured current about `0.1 mA`, voltage about `32.054 V`.
- Mini DMA now stops current-sweep voltage-limit unwinds when measured current collapses near zero at the voltage limit, writes a terminal `wire_break_or_contact_loss` trace row, and avoids the mechanical recovery seek that previously kept moving after an electrical fault.
- Mini DMA predictive stiffness control is now phase-scoped: active current-sweep/iso-current/elastocaloric/setup phases keep adaptive damping, while ordinary idle/manual seeks use the configured nudge unless explicit calibrated or live stiffness is available.
- Mini DMA now clears naturally finished IR worker/thread references and tolerates already-deleted Qt wrappers during disconnect/close cleanup.
- Mini DMA saved Builder project import cleanup now clears pending retry state and tolerates already-deleted Qt thread wrappers.
- Mini DMA Builder project suggestions now reuse a fresh parsed-project cache immediately when background import starts, so microwire completers stay populated while exact diameter/current matching continues in the worker.
- Mini DMA run-quality CLI can now emit per-run core PNG/JSON artifacts in the same pass as `run_quality.json`, so optimization and normal-run follow-up can use one repeatable diagnostic command.
- Mini DMA run-quality batch core-plot generation now keeps going when one run folder is incomplete, reporting `plot_error=...` while still writing `run_quality.json`.
- AC UI cleanup is a first coherent pass, not a full rewrite.
- AC worker failures now surface run-status sidecar/fallback details in the UI, including rows written and local fallback status path when an output drive disappears.
- AC completed and stopped sweep messages now also include run-status sidecar details, including rows written and status-file location.
- AC output status now shows the planned run-status sidecar and local fallback paths before the sweep starts.
- AC shared-broker current-source failures now use the same operator-facing broker diagnostics as Mini DMA and Current Annealing.
- AC sweep-start failures now leave the UI idle and show a warning when PSU preparation or worker startup fails.
- Elastocaloric recipe design recommends tightening the existing `ELASTOCALORIC_EFFECT` scaffold rather than adding another parallel recipe engine; the current branch now has software-only dropdown/build/round-trip coverage and screenshot evidence for that workflow.

## Remaining Work

- Add live validation only after the user explicitly authorizes bench work.
- Consider a deeper AC UI rewrite after the user reviews this first pass.
