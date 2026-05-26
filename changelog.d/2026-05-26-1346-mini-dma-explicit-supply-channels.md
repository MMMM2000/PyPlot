2026-05-26 13:46

- Changed Mini DMA supply setup so current-sweep and motor-supply channels start unselected instead of using profile defaults.
- Added a shared-broker connection health check before Mini DMA reports the broker supply as connected.
- Let Mini DMA manual auto-connect start a local shared HMP broker when the broker endpoint is down and the operator has explicitly selected the HMP COM port plus supply channels.
- Reordered Mini DMA manual auto-connect so the HMP motor-supply rail is enabled before checking Tic VIN.
- Improved Mini DMA guardrails so current output and motor power cannot be prepared until the operator explicitly selects the wired HMP channels.
