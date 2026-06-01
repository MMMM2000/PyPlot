2026-05-28 17:19

- Added broker-side scheduled HMP polling with timestamped cached readbacks for shared Current Annealing and Mini DMA operation.
- Added coalesced scheduled current setpoints so stale intermediate current commands do not build up when clients update faster than the HMP can service SCPI requests.
