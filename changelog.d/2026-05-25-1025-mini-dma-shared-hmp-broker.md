2026-05-25 10:25

- Added optional Shared HMP broker mode to Mini DMA Logger so it can use channel-scoped broker leases for current-sweep and motor-supply HMP channels while preserving direct serial supply profiles.
- Added Mini DMA broker host/port settings and preflight behavior that keeps shared-broker mode from silently switching back to serial auto-detect.
