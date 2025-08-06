#!/usr/bin/env python3
"""Simple backend that emits sample log entries as JSON."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
import random


def generate_logs(count: int = 10) -> list[dict[str, object]]:
    """Return ``count`` fake log entries with timestamps and values."""
    base = datetime.now()
    return [
        {
            "timestamp": (base + timedelta(seconds=i)).isoformat(),
            "value": round(random.random() * 2.0, 3),
        }
        for i in range(count)
    ]


if __name__ == "__main__":
    print(json.dumps(generate_logs()))
