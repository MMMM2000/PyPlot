2026-06-18 18:25

- Hardened the shared HMP broker so active channel leases cannot be overwritten by role or profile changes, and so same-owner reconnects reuse the existing lease instead of replacing it.
- Added AC susceptibility to the shared-HMP setup role list and capped HMP-backed AC voltage limits to the physical HMP range.
- Made Mini DMA shared-broker disconnect explicitly switch leased current and motor channels off before releasing them.
- Kept Iso-stress fatigue recipes on the expected up-and-back current sweep even if a stale hidden one-way optimization setting is present.
- Preserved the Current Annealing shared-broker port setting when reopening the app and removed duplicate UI signal connections found during release review.
