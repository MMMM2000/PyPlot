2026-07-13 14:50

- Load Microwire Data Builder projects transactionally so section, transition-review, and Assemble-view state is restored exactly if any import step fails or the window closes mid-load.
- Keep omitted project sections blank, restore annealing phase/visibility review state, and prevent old deferred writes or peer-section caches from leaking into a newly loaded project.
- Keep automatic startup-load failures non-blocking while preserving modal error feedback for manual project opens.
