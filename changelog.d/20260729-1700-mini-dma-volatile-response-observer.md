- Add an experimental, non-UI Mini DMA iso-stress volatile-response observer
  behind `MINI_DMA_VOLATILE_RESPONSE_OBSERVER=1`. Repeated unobservable motor
  responses now earn a bounded no-move observation window and cycle-centered
  correction, while detected large-strain transformation activity keeps the
  established fast controller path.
