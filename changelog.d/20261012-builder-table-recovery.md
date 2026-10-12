Added explicit, copy-only recovery for Builder packages whose table bytes were
edited without updating their manifest descriptors. Normal imports retain strict
checksum/size validation; recovery rejects non-table corruption, never overwrites
the source or an existing destination, and fully verifies the recovered package.
