### Fixed

- Prevent TMA length setup and recipe motion from starting when the 1 mA continuity check reports an open circuit, including during dedicated-controller startup before the recipe becomes active.
- Validate the Prague/Košice IPC policy after child-process hardware auto-detection so a Košice KERN scale is not rejected using the child's initial Prague defaults.
