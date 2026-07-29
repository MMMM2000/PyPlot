- Add an experimental, non-UI Mini DMA iso-stress volatile-response observer
  behind `MINI_DMA_VOLATILE_RESPONSE_OBSERVER=1`. Repeated unobservable motor
  responses now earn a bounded no-move observation window and cycle-centered
  correction, while detected large-strain transformation activity keeps the
  established fast controller path.
- Record the observer capability and opt-in state explicitly in run metadata.
- Validate the guarded branch on a remounted bad wire with completed
  1->10->1 mA and 1->40->1 mA hardware loops; the conservative observer
  remained dormant and is not enabled by default.
