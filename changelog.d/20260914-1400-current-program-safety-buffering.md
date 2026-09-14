### Current Program Logger

- Add opt-in specimen-configured short-circuit and sustained low-current/contact-loss detectors. Both stop output and latch a fault requiring acknowledgement before another run; legacy maximum-resistance actions remain separate.
- Add optional measurement CSV logging, a separate CSV rate (default 1 Hz), a bounded asynchronous writer, and a configurable 100–20,000-point live history (default 10,000). Coalesce outstanding live UI updates and bound retained sequence events.
- Keep Windows awake for a run while allowing display sleep; acquire before hardware activation and release after shutdown. This does not replace an independent interlock or prevent explicit sleep/shutdown.
- Prefer the connected matching-vendor VISA resource on Refresh and reject a Keithley USB address in Siglent mode with an actionable message.
