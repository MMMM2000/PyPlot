Fixed
-----
- Preserve text-only serial settings when reconstructing the dedicated TMA controller, so Košice KERN configurations retain their selected baud rate and control policy.
- Verify that the UI scale worker has released its serial port before transferring TMA hardware ownership, and fail preflight when the child cannot actually open the configured scale port.
