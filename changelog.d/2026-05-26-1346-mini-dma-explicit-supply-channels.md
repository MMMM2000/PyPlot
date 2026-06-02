2026-05-26 13:46

- Changed Mini DMA supply setup so current-sweep and motor-supply channels start unselected instead of using profile defaults.
- Added a shared-broker connection health check before Mini DMA reports the broker supply as connected.
- Let Mini DMA manual auto-connect start a local shared HMP broker when the broker endpoint is down and the operator has explicitly selected the HMP COM port plus supply channels.
- Reordered Mini DMA manual auto-connect so the HMP motor-supply rail is enabled before checking Tic VIN.
- Improved Mini DMA guardrails so current output and motor power cannot be prepared until the operator explicitly selects the wired HMP channels.
- Added the bundled 64-bit `libusb` wheel and updated the Tic native USB backend loader so Mini DMA can prefer native PyUSB Tic commands before falling back to `ticcmd`.
- Let Mini DMA native Tic USB accept a single visible Tic when Windows/libusb cannot read USB string descriptors, while still rejecting ambiguous multi-Tic scans.
- Made preferred-native Tic control fall back to `ticcmd` if an individual native USB status or move command is denied.
- Tightened Mini DMA Tic status handling so device-list output can no longer be treated as motor status; status must include parseable VIN before motor power is verified.
- Logged Tic transport use so native USB activation and every `ticcmd` fallback reason are visible in Mini DMA run logs.
- Serialized native Tic USB status and motion/keepalive commands so status refreshes cannot race motion commands and incorrectly mark motor VIN as unavailable.
- Hid Windows console windows for rare `ticcmd` fallback commands.
- Showed the hardware auto-connect progress dialog during Start recipe preflight when required hardware is not already ready.
- Added `scripts/run_mini_dma_shared_hmp_checks.ps1` for a fast shared-HMP/Mini-DMA/Tic regression slice.
