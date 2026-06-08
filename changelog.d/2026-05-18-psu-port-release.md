2026-05-18 13:35 UTC

- Fixed AC microwire current sweeps failing to start when the selected PSU COM port was already open by the logger's inherited connection controls.
- Normalized Windows serial resource strings so malformed COM path variants are passed to pyserial as plain COM port names.
