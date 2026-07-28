### Changed

- Added an offline-only processed-observation policy to the Mini DMA iso-stress
  closed-loop simulator, including fixed-current cadence, robust center/trend,
  noise, and motor-reversal gates. Documented the run 15 hardware result and
  rejected the policy for live use after a finalized transforming Prague trace
  showed that the trigger was not transformation-specific.
