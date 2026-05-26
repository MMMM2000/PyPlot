# Shared HMP Power Supply Broker

The shared HMP broker is the foundation for running multiple bench tools against one multi-channel HMP supply without letting independent programs write to the same serial command stream. It supports HMP4030 and HMP4040 supplies through the same HMP40xx model layer.

The first implementation adds the broker, JSON-line localhost protocol, fake-driver tests, and a setup utility. Current Annealing Logger and Mini DMA Logger can opt into the shared broker path while keeping their existing direct serial supply modes.

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
- Mini DMA motor supply
- Mini DMA current sweep
- Current annealing
- Other/manual

Saved profiles are bench memory, not silent defaults. Loading a profile can pre-fill known wiring, but the setup utility requires review when the model, port identity, or confirmation state changes before output enable is allowed.

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
- `set_current`
- `set_output`
- `measure_channel`
- `snapshot`

Current Annealing Logger exposes **Shared HMP broker** as an optional supply profile. In that mode it leases the selected channel with the `Current annealing` role, configures only that channel on start, reads broker voltage/current snapshots, sends current setpoints through the broker, and turns off/releases only the leased channel on stop. Its raw serial command box is disabled in broker mode.

Mini DMA Logger exposes **Shared HMP broker** as an optional current-annealing supply profile. In that mode it connects to the localhost broker instead of opening the HMP serial port directly, leases the configured current-sweep channel with the `Mini DMA current sweep` role, and leases the motor-supply channel with the `Mini DMA motor supply` role only when that channel is configured. Direct HMP4030/HMP4040 serial profiles remain available for non-shared benches.
