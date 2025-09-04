from __future__ import annotations
import os, sys, pathlib
from typing import List

def _split_bar_list(s: str) -> List[str]:
    return [p for p in (s or "").split("|") if p]

def _normalize_files(files_bar: str) -> List[str]:
    files = _split_bar_list(files_bar)
    if not files:
        return []
    first = files[0]
    first_p = pathlib.Path(first)
    base_dir = first_p.parent if first_p.parent.as_posix() not in ("", ".") else pathlib.Path.cwd()
    out = []
    for f in files:
        p = pathlib.Path(f)
        if not p.is_absolute():
            p = base_dir / p
        out.append(str(p.resolve()))
    return out

def run_from_labtalk(params: dict):
    files = _normalize_files(params.get("__files$", ""))
    print(">>> python_code.run_from_labtalk (diagnostic)")
    print("FILES:")
    for f in files:
        print(" -", f)
    print("VARS:", params.get("__vars$", "sum,dT"))
    print("BASELINE:", params.get("__baseline$", "first"))
    print("DELTA:", params.get("__dlt", 0))

if __name__ == "__main__":
    print("python_code.py invoked (diagnostic stub).")
