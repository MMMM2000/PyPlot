# Shared HMP Power Supply Broker

The shared HMP broker is the foundation for running multiple bench tools against one multi-channel HMP supply without letting independent programs write to the same serial command stream. It supports HMP4030 and HMP4040 supplies through the same HMP40xx model layer.

The shared HMP broker adds channel leases, a JSON-line localhost protocol, fake-driver tests, and logger integrations. Current Annealing Logger and Mini DMA Logger can opt into the shared broker path while keeping their existing direct serial supply modes.

## Safety Model

- One broker owns the physical HMP serial connection.
- Clients request channel-scoped actions such as lease, configure, set current, set output, and measure.
- Raw SCPI is not exposed in shared mode.
- Stateful HMP sequences are serialized as one broker-owned operation: `INST:NSEL <channel>` followed by the channel command or query.
- Global commands such as `*RST`, `OUTP:GEN 0`, `SYST:LOC`, and all-output-off actions are blocked outside an explicit emergency path.
- A channel cannot be leased or controlled until an operator confirms what is physically wired to that channel.

## Supported Models

| Model | Channels | Notes |
| --- | ---: | --- |
| HMP4030 | 3 | CH1 through CH3 are valid. CH4 requests are rejected. |
| HMP4040 | 4 | CH1 through CH4 are valid. |

Both models use the same relevant channel-selection pattern, `INST:NSEL <channel>`, for broker-controlled channel operations.

## Bench Wiring

The broker profile shows only the channels that exist on the selected model and assigns each channel one role:

- Unused
- Mini DMA motor supply
- Mini DMA current sweep
- Current annealing
- Other/manual

Saved profiles are bench memory, not silent defaults. Logger UIs still start channel selectors at **Select channel...** after model changes or detection, and the operator must choose the physically wired channel before output enable is allowed. Routine broker startup now happens from Current Annealing or Mini DMA after those explicit channel choices, instead of through a separate launcher entry.

## Current HMP4040 Bench Example

The current physical setup can be saved as a named bench profile, for example `Kosice HMP4040 bench`:

| Channel | Role |
| --- | --- |
| CH1 | Available for current annealing |
| CH2 | Available or spare |
| CH3 | Mini DMA motor supply |
| CH4 | Mini DMA current sweep |

This profile should still be reviewed at the bench before enabling outputs, especially after rewiring, changing COM ports, or swapping between HMP4030 and HMP4040.

## Integration Notes

The broker API is intentionally channel-scoped. Logger integrations replace direct app-owned HMP serial calls with broker client calls:

- `lease`
- `release`
- `configure_channel`
- `set_current`, scheduled/coalesced `schedule_current`, or rate-limited `schedule_current_ramp`
- `set_output`
- `measure_channel`
- `configure_polling`
- `latest_readback`
- `snapshot`

For shared measurements, the broker can run a lightweight scheduler. Clients configure a polling interval for the channels that need live readback, then consume timestamped cached readbacks with `latest_readback` instead of forcing every app request to perform a fresh HMP `MEAS` query. Current setpoint updates can be submitted through `schedule_current`; the broker keeps only the newest pending current per channel and applies it on the next scheduler tick before polling. This prevents stale intermediate current commands from building a backlog when a GUI loop runs faster than the HMP can service SCPI requests.

Current ramps should use `schedule_current_ramp`. The broker quantizes requested ramp rates and targets to the supply current resolution, sends at most one configured resolution-sized step per scheduler pass, and does not catch up a delayed tick by issuing a larger current jump. Direct setpoints and ramp setpoints override each other per channel, so a manual/direct safety setpoint cannot leave an older ramp active behind it.

The first scheduled mode targets reliable dual-logger `1 Hz` readback rather than high-rate acquisition. CH1/CH4 current paths are normally polled at about `1 s`; CH3 motor-supply readback should remain slower or explicit because it is mainly a rail-health check, not a per-point measurement channel.

Current Annealing Logger defaults to **Shared HMP broker**. In that mode it leases the selected channel with the `Current annealing` role, configures only that channel on start, reads broker voltage/current snapshots, sends current ramp targets through the broker scheduler when available, and turns off/releases only the leased channel on stop. Start can auto-connect or start the broker after the operator has selected a real channel; it does not guess the wiring. Its raw serial command box is disabled in broker mode, and obsolete hold-current actions fall back to reversing toward the start current.

Mini DMA Logger exposes **Shared HMP broker** as an optional current-annealing supply profile. In that mode it connects to the localhost broker instead of opening the HMP serial port directly, leases the configured current-sweep channel with the `Mini DMA current sweep` role, and leases the motor-supply channel with the `Mini DMA motor supply` role only when that channel is configured. During recipe current sweeps it passes the recipe ramp rate to the broker so delayed GUI/control ticks do not produce oversized current jumps at the PSU. Direct HMP4030/HMP4040 serial profiles remain available for non-shared benches.

Mini DMA does not use profile-default output channels. The current-sweep and motor-supply channel selectors start at **Select channel...**, and changing or auto-detecting a supply profile clears the channel selectors. Operators must choose the real wired channels before preparing current output or enabling motor power.

Shared-broker Mini DMA connect performs a broker `snapshot` request before reporting the supply connected. If the broker is not responding and the operator selected an HMP COM port plus explicit Mini DMA channels, Mini DMA starts a local broker for those confirmed channels and then connects through it. This catches genuinely blocked brokers at connect time while still letting the normal manual auto-connect path bring up the shared broker.

Mini DMA manual hardware auto-connect powers the selected HMP motor-supply channel before checking Tic VIN. That keeps the hardware-card result aligned with benches where the Tic motor rail is supplied by the same HMP channel that auto-connect is responsible for enabling.

Mini DMA prefers native USB for Pololu Tic commands when **Prefer native USB commands when available** is checked. The Python runtime includes the `libusb` wheel so PyUSB can load a matching 64-bit `libusb-1.0.dll`; `ticcmd` remains the fallback path if the native USB backend, device scan, or individual native USB command is unavailable. If Windows/libusb cannot read the Tic string descriptors but exactly one Tic is visible, Mini DMA accepts that device for native control. Tic status checks must return a parseable `VIN voltage` before Mini DMA treats motor power as verified; device-list output is not accepted as motor status. The run log records when native USB is active and every fallback to `ticcmd`, including the reason, so fallback use during a run should be visible and rare.

When Mini DMA must use `ticcmd`, it launches it without a visible Windows console. Recipe start uses the same auto-connect progress dialog as the manual hardware button while it is trying to connect missing hardware.

For quick regression checks during shared-HMP/Mini-DMA work, run `scripts/run_mini_dma_shared_hmp_checks.ps1`. It uses a workspace temp root and covers the explicit-channel guardrails, broker controller path, motor-supply-before-Tic ordering, native USB backend, manual jog status freshness, and the Current Annealing shared-broker smoke tests without running the full Mini DMA test file.

For end-to-end bench checks, use `docs/shared_hmp_bench_validation.md`. It captures the current CH1/CH3/CH4 wiring, low-current/no-wire test limits, motor-supply checks, small-motion smoke, connected-current smoke, and final HMP safety readback.
