2026-05-18 13:55 UTC

- Made AC microwire current sweeps fail fast when PSU readback does not confirm actual current flow.
- Logged a failure row before aborting so misleading requested-current data is not mistaken for delivered-current data.
