# Shared HMP Power Supply Broker

The shared HMP broker is the foundation for running multiple bench tools against one multi-channel HMP supply without letting independent programs write to the same serial command stream. It supports HMP4030 and HMP4040 supplies through the same HMP40xx model layer.

The first implementation adds the broker, JSON-line localhost protocol, fake-driver tests, and a setup utility. Current Annealing Logger and TMA Logger can opt into the shared broker path while keeping their existing direct serial supply modes.

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

## Setup Utility

Open **Shared HMP PSU Setup** from the launcher to review a bench profile. The utility shows only the channels that exist on the selected model and lets the operator assign each channel one role:

- Unused
- TMA motor supply
- TMA current sweep
- Current annealing
- Other/manual

Saved profiles are bench memory, not silent defaults. Loading a profile can pre-fill known wiring, but the setup utility requires review when the model, port identity, or confirmation state changes before output enable is allowed.

## Current HMP4030 Bench Example

The current physical setup can be saved as a named bench profile, for example `Kosice HMP4030 bench`:

| Channel | Role |
| --- | --- |
| CH1 | Available for current annealing |
| CH2 | TMA motor supply |
| CH3 | TMA current sweep |

This profile should still be reviewed at the bench before enabling outputs, especially after rewiring, changing COM ports, or swapping between HMP4030 and HMP4040.

## Integration Notes

The broker API is intentionally channel-scoped. Logger integrations replace direct app-owned HMP serial calls with broker client calls:

- `lease`
- `release`
- `configure_channel`
- `set_current`
- `set_output`
- `measure_channel`
- `preview_polling`
- `configure_polling`
- `latest_readback`
- `schedule_current`
- `snapshot`

## Readback cadence and simultaneous loggers

The broker owns a single bounded scheduler with 2 Hz total fresh HMP readback capacity. Current Annealing and TMA expose two operator choices:

- **1 Hz (fixed)** requests one fresh readback per second.
- **Up to 2 Hz (1 Hz when shared)** requests two fresh readbacks per second while the logger is the only active poller.

When a second 2 Hz logger starts, the broker previews the allocation before output enable. The starting app warns that both clients will run at 1 Hz and lets the operator cancel. If accepted, both apps show and log the effective 1 Hz cadence. When either lease is released, the remaining 2 Hz client returns to 2 Hz automatically. Current setpoints are coalesced separately from readback polling, so a slow query cannot build an unbounded command queue.

Current Annealing changes its command timer together with the effective cadence. At 2 Hz it uses resolution-aware alternating setpoints whose average slope remains the configured mA/s value; changing to 1 Hz therefore does not change the physical ramp rate. TMA recipe timing and the separate Prague and Košice control policies are unchanged by readback arbitration.

Cadence state carries a monotonically increasing generation, requested/effective rates, and a bounded transition-event history in the broker snapshot. Logger metadata records the requested and effective rate at run start.

## USB-D preference

Serial discovery ranks the HMP native USB-D virtual COM interface ahead of generic USB-to-RS232 adapters. The native interface is recognized by the HAMEG HO720 identity, including USB VID/PID `0403:ED72`, while all other ports remain available as fallbacks. This is a connection preference only: both transports use the same SCPI operations and the broker retains the measured 2 Hz total fresh-readback budget.

Current Annealing Logger exposes **Shared HMP broker** as an optional supply profile. In that mode it leases the selected channel with the `Current annealing` role, configures only that channel on start, reads broker voltage/current snapshots, sends current setpoints through the broker, and turns off/releases only the leased channel on stop. Its raw serial command box is disabled in broker mode.

TMA Logger exposes **Shared HMP broker** as a supply profile. In that mode it connects to the localhost broker instead of opening the HMP serial port directly, leases the configured current-sweep channel with the `TMA current sweep` role, and leases the motor-supply channel with the `TMA motor supply` role only when that channel is configured. Direct HMP4030/HMP4040 serial profiles remain available for non-shared benches.

TMA does not use profile-default output channels for direct HMP profiles. The current-sweep and motor-supply channel selectors start at **Select channel...**, and changing or auto-detecting a direct supply profile clears the channel selectors. In shared-broker mode, manual auto-connect and recipe preflight may fill the current Košice bench default of CH3 current sweep and CH2 motor supply before checking Tic VIN, but operators should still review those channels after any rewiring.

Shared-broker TMA connect performs a broker `snapshot` request before reporting the supply connected. Automatic hardware preflight also checks the standard localhost endpoint before scanning serial supplies; when a broker is already running, TMA adopts it even if an older saved setting names a direct HMP profile. This prevents TMA and Current Annealing from competing for the broker-owned COM port. The explicit direct supply connect action remains available for non-shared benches. If the broker is not responding and the operator selected an HMP COM port plus explicit TMA channels, TMA can start a local broker for those confirmed channels and then connects through it.

TMA manual hardware auto-connect powers the selected HMP motor-supply channel before checking Tic VIN. That keeps the hardware-card result aligned with benches where the Tic motor rail is supplied by the same HMP channel that auto-connect is responsible for enabling.

TMA prefers native USB for Pololu Tic commands when **Prefer native USB commands when available** is checked. The Python runtime includes the `libusb` wheel so PyUSB can load a matching 64-bit `libusb-1.0.dll`; `ticcmd` remains the fallback path if the native USB backend, device scan, or individual native USB command is unavailable. If Windows/libusb cannot read the Tic string descriptors but exactly one Tic is visible, TMA accepts that device for native control. Tic status checks must return a parseable `VIN voltage` before TMA treats motor power as verified; device-list output is not accepted as motor status. The run log records when native USB is active and every fallback to `ticcmd`, including the reason, so fallback use during a run should be visible and rare.

When TMA must use `ticcmd`, it launches it without a visible Windows console. Recipe start uses the same auto-connect progress dialog as the manual hardware button while it is trying to connect missing hardware.

For quick regression checks during shared-HMP/Mini-DMA work, run `scripts/run_mini_dma_shared_hmp_checks.ps1`. It uses a workspace temp root and covers the explicit-channel guardrails, broker controller path, motor-supply-before-Tic ordering, native USB backend, manual jog status freshness, and the Current Annealing shared-broker smoke tests without running the full TMA test file.

For end-to-end bench checks, use `docs/shared_hmp_bench_validation.md`. It captures the current CH1/CH2/CH3 wiring, low-current/no-wire test limits, motor-supply checks, small-motion smoke, connected-current smoke, and final HMP safety readback.
