Fixed project saves and loads for long TMA fatigue acquisitions containing more
than 50 million cells. The columnar codec keeps every measurement field and
point under bounded frame, typed-array, byte, node and container limits, without
splitting a run into additional experiments.
