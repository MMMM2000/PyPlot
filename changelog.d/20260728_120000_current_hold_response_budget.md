Mini DMA iso-stress current holds now allow only one unobserved motor correction at a
time. Adaptive response learning is consumed once per completed post-move observation,
preventing delayed scale feedback from falsely earning repeated correction growth.
Campaign git checks also keep non-fatal filesystem warnings separate from porcelain
status output, avoiding false dirty-worktree failures before guarded hardware runs.
