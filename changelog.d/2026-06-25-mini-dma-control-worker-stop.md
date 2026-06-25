2026-06-25 09:45

- Mini DMA control-trace file write failures no longer stop active recipes; tracing is disabled and the recipe continues when the trace file handle fails.
- Mini DMA control-worker crashes now finalize the run through the normal stop path so metadata stop reasons and summary images are still generated.
