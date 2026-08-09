- TMA iso-stress current holds now measure an unchanged processed-response timeout
  from the correction that opened the response window, independently of the
  fresh-sample consumption clock. Constant scale feedback can therefore trigger
  a bounded retry after the configured window instead of waiting indefinitely.
