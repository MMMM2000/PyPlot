from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

NUMERIC = re.compile(r"^[\s\t]*[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?")
SPLIT = re.compile(r"\t+|\s{2,}")  # tabs or 2+ spaces


def _is_numeric_line(line: str) -> bool:
    return bool(NUMERIC.match(line))


def _try_float(tok: str) -> float | None:
    try:
        return float(tok)
    except Exception:
        return None


def parse_dma_txt(filepath: Path) -> Dict[int, Tuple[List[float], List[float]]]:
    """Parse a TA DMA exported .txt file for IsoStress data."""
    datasets: Dict[int, Tuple[List[float], List[float]]] = {}

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if "IsoStress" in line:
            # Found an IsoStress block: find the header that follows.
            j = i + 1
            header_line_idx = -1
            while j < n and not lines[j].strip().startswith("[step]"):
                if lines[j].strip().startswith("Step time"):
                    header_line_idx = j
                    break
                j += 1

            if header_line_idx == -1:
                i += 1
                continue

            header = SPLIT.split(lines[header_line_idx].strip())
            k = header_line_idx + 1

            # Skip units line.
            if k < n and ("°C" in lines[k] or "%" in lines[k] or "MPa" in lines[k]):
                k += 1

            temp_idx = next(
                (
                    idx
                    for idx, name in enumerate(header)
                    if name.strip().lower().startswith("temperature")
                ),
                -1,
            )
            strain_idx = next(
                (
                    idx
                    for idx, name in enumerate(header)
                    if name.strip().lower() == "strain"
                ),
                -1,
            )
            stress_idx = next(
                (
                    idx
                    for idx, name in enumerate(header)
                    if name.strip().lower() == "stress"
                ),
                -1,
            )

            if -1 in (temp_idx, strain_idx, stress_idx):
                i = k
                continue

            temps: List[float] = []
            strains: List[float] = []
            stresses: List[float] = []
            while k < n:
                row_line = lines[k].strip()
                if not row_line or not _is_numeric_line(row_line):
                    break
                toks = SPLIT.split(row_line)
                if max(temp_idx, strain_idx, stress_idx) < len(toks):
                    temp = _try_float(toks[temp_idx])
                    strain = _try_float(toks[strain_idx])
                    stress = _try_float(toks[stress_idx])
                    if all(v is not None for v in (temp, strain, stress)):
                        temps.append(temp)
                        strains.append(strain)
                        stresses.append(stress)
                k += 1

            if stresses:
                avg_stress = round(sum(stresses) / len(stresses))
                if avg_stress not in datasets:
                    datasets[avg_stress] = ([], [])
                datasets[avg_stress][0].extend(temps)
                datasets[avg_stress][1].extend(strains)
            i = k
        else:
            i += 1

    return datasets

