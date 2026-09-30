"""Length mapping shared by the manual video review surfaces."""
from __future__ import annotations

import math
from typing import Mapping


def cumulative_piece_lengths(
    lengths: Mapping[tuple[str, int, int], float | None],
) -> dict[tuple[str, int, int], float | None]:
    grouped: dict[tuple[str, int], list[tuple[int, float | None]]] = {}
    for (composition, draw, piece), value in lengths.items():
        if draw > 0 and piece > 0:
            grouped.setdefault((composition, draw), []).append((piece, value))
    result: dict[tuple[str, int, int], float | None] = {}
    for (composition, draw), pieces in grouped.items():
        running: float | None = 0.0
        expected = 1
        for piece, value in sorted(pieces):
            if (piece != expected or running is None or value is None
                    or not math.isfinite(value) or value <= 0):
                running = None
            else:
                running += value
            result[(composition, draw, piece)] = running
            expected = piece + 1
    return result


def video_piece_range(end: float, cumulative: float, length: float) -> str | None:
    if (not all(math.isfinite(v) for v in (end, cumulative, length))
            or end <= 0 or length <= 0 or cumulative < length - 1e-9
            or cumulative > end + 1e-9):
        return None
    low = max(0.0, end - cumulative)
    high = end - (cumulative - length)
    return f"{low:.12g}-{high:.12g}"
