## 2026-11-11 UTC

- Accept positive physical piece labels such as s1 in general logger naming,
  retaining legacy labels such as s2-1.
- Add an explicit local physical-sample chooser to general, manual stress-strain
  and TMA naming fields. It preserves remeasurement IDs, rejects duplicate local
  creation and reports imported identity conflicts without silent renumbering.
- This is a local-only first stage, not a cross-device allocation service or a
  complete historical sample inventory. Other logger adapters and structured
  UUID export remain to be implemented.
