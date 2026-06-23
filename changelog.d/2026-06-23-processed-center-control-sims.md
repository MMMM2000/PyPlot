2026-06-23 13:52

- Tightened Mini DMA current-hold processed-signal bands so large raw fluctuations centered near target do not cause chasing, while a biased processed center still triggers recovery.
- Disabled cruise feedback for Mini DMA current-sweep load/stress control and added endpoint recovery checks before current sweep or unwind steps can complete.
- Expanded the Mini DMA wire simulator with processed-center scenario-matrix reports covering noise, transformation, slack, stiffness, wire diameter, and delayed-feedback cases.
- Kept grouped strain/current core plots from dropping first-overheating rows that do not have a numeric plateau index.
