"""Manual microscope diameter overrides for common microwire draws."""

from __future__ import annotations

from typing import Dict, List, Tuple

# (composition, draw, piece, d_um, D_um)
_MANUAL_ROWS: List[Tuple[str, int, int, float | None, float | None]] = [
    ("Ni46Fe23Ga23Co8", 1, 1, 6.7, 34.4),
    ("Ni46Fe23Ga23Co8", 2, 3, None, None),
    ("Ni46Fe31Ga15Si8", 1, 5, 15.3, 43.8),
    ("Ni47Fe24Ga23Co6", 2, 1, 15.1, None),
    ("Ni47Fe24Ga23Co6", 2, 2, 8.7, 41.9),
    ("Ni47Fe30Ga17Si6", 1, 1, None, None),
    ("Ni47Fe30Ga17Si6", 1, 8, 10.4, 55.1),
    ("Ni48Fe25Ga23Co4", 1, 6, 7.7, 44.2),
    ("Ni48Fe25Ga27", 1, 2, 16.5, 65.1),
    ("Ni48Fe25Ga27", 2, 3, 8.1, 42.4),
    ("Ni48Fe25Ga27", 2, 5, None, None),
    ("Ni48Fe27Ga21Cu2", 2, 4, 15.6, 37.6),
    ("Ni49Fe28Ga21Si2", 1, 5, 10.3, 60.1),
    ("Ni48Fe29Ga19Si4", 1, 1, 14.5, 43.5),
    ("Ni48Fe29Ga19Sn4", 2, 1, 10.5, 41.3),
    ("Ni46Fe31Ga15Si8", 1, 10, 19.9, 60.8),
    ("Ni49Fe25Ga26", 1, 2, 11.1, 34.1),
    ("Ni49Fe25Ga26", 1, 3, 10.8, 35.9),
    ("Ni49Fe28Ga21Si2", 1, 9, 11.2, 71.2),
    ("Ni50Fe25Ga25", 2, 1, 9.3, 59.9),
    ("Ni50Fe25Ga25", 2, 3, 19.6, 54.0),
    ("Ni50Fe25Ga25", 3, 1, 12.5, 42.5),
    ("Ni50Fe26Ga24", 1, 2, 8.3, 25.6),
    ("Ni50Fe26Ga24", 2, 2, 7.5, 56.1),
    ("Ni50Fe26Ga24", 2, 4, 10.0, 73.6),
    ("Ni50Fe27Ga23", 2, 6, 9.2, 32.6),
    ("Ni50Fe27Ga23", 5, 4, 19.4, 58.6),
    ("Ni50Fe27Ga23", 6, 2, 11.3, 55.6),
    ("Ni50Fe27Ga23", 6, 4, 7.8, 66.7),
    ("Ni50Fe27Ga23", 6, 6, 15.2, 47.1),
    ("Ni50Fe27Ga23", 7, 1, 9.9, 45.4),
    ("Ni50Fe27Ga23", 9, 1, 10.9, None),
    ("Ni50Fe27Ga23", 9, 3, 15.6, 71.8),
    ("Ni50Fe27Ga23", 10, 1, 12.4, 96.8),
    ("Ni50Fe27Ga23", 10, 4, 13.7, 70.8),
    ("Ni50Fe27Ga23", 10, 5, 14.2, 101.6),
    ("Ni50Fe27Ga23", 11, 1, 14.0, 48.7),
    ("Ni50Fe27Ga23", 12, 2, 19.1, 58.6),
    ("Ni50Fe28Ga22", 2, 1, 11.3, 42.8),
    ("Ni51Fe25Ga24", 1, 1, 11.2, 37.5),
    ("Ni51Fe25Ga24", 1, 5, 7.8, 27.5),
    ("Ni51Fe26Ga21", 1, 2, 11.6, 57.1),
    ("Ni52Fe15Ga27Co6", 1, 1, 17.5, 64.1),
    ("Ni52Fe15Ga27Co6", 2, 1, 5.9, 65.6),
    ("Ni52Fe21Ga27", 2, 1, 11.7, 35.5),
    ("Ni53Fe16Ga27Co4", 1, 2, 9.4, 55.1),
    ("Ni54Fe17Ga27Co2", 1, 2, 11.7, 44.6),
    ("Ni54Fe19Ga27", 1, 1, 14.3, 48.9),
    ("Ni55Fe18Ga27", 4, 1, 15.3, 104.9),
    ("Ni55Fe18Ga27", 4, 2, 20.5, None),
]


MANUAL_DIAMETER_OVERRIDES: Dict[Tuple[str, int, int], Dict[str, float]] = {}

for composition, draw, piece, d_value, D_value in _MANUAL_ROWS:
    key = (composition, draw, piece)
    entry = MANUAL_DIAMETER_OVERRIDES.setdefault(key, {})
    if d_value is not None and "d" not in entry:
        entry["d"] = float(d_value)
    if D_value is not None and "D" not in entry:
        entry["D"] = float(D_value)


__all__ = ["MANUAL_DIAMETER_OVERRIDES"]

