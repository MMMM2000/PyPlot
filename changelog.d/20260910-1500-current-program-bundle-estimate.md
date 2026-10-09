## Current Program Logger: 2 kg bundle estimate

- Add a persistent supported-load-per-wire input, rounded-up wire count for 2 kg, and per-wire/total electrical power at the edited sequence's maximum current (including initial current).
- Use the displayed run's measured resistance closest to that current, with the measurement current and live/history source shown explicitly. Power remains unavailable without valid resistance data. This is an I²R estimate, not measured peak power, validated stroke, or a safety limit; equal load sharing is assumed and fixture mass/safety margin are excluded.
- Software-only tests cover unit conversion, ceiling, invalid inputs, recipe updates, resistance selection, and settings persistence.
