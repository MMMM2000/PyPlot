"""Export reviewed CA/TMA decisions from a project copy into run sidecars.

Dry-run is the default.  Existing non-identical sidecars are never overwritten.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from microwire_data_builder.project_package import load_project
from plotting.shared.transition_review import (
    atomic_write_review,
    load_review,
    sidecar_path_for_measurement,
    utc_now_text,
)
from plotting.shared.transition_review_adapters import (
    current_annealing_review_draft,
    tma_review_draft,
)


def _records(section: Mapping[str, Any], key: str) -> dict[str, dict[str, Any]]:
    extra = section.get("extra")
    if not isinstance(extra, Mapping):
        return {}
    raw = extra.get(key)
    if not isinstance(raw, Mapping):
        return {}
    values = raw.get("records", raw)
    if not isinstance(values, Mapping):
        return {}
    return {
        str(record_id): dict(payload)
        for record_id, payload in values.items()
        if isinstance(payload, Mapping)
    }


def _build_name_index(
    roots: Iterable[Path],
    wanted_names: Iterable[object],
) -> dict[str, list[Path]]:
    wanted = {
        Path(str(value)).name.casefold()
        for value in wanted_names
        if str(value or "").strip()
    }
    index: dict[str, list[Path]] = defaultdict(list)
    if not wanted:
        return index
    for root in roots:
        for path in root.rglob("*"):
            key = path.name.casefold()
            if key in wanted:
                index[key].append(path)
    return index


def _candidate(
    path_text: object,
    names: Iterable[object],
    roots: list[Path],
    *,
    name_index: Mapping[str, Iterable[Path]] | None = None,
) -> tuple[Path | None, str]:
    text = str(path_text or "").strip()
    outside_roots = False
    if text:
        direct = Path(text)
        if direct.exists():
            resolved = direct.resolve()
            if any(resolved == root or resolved.is_relative_to(root) for root in roots):
                return resolved, "exact_path"
            outside_roots = True
    wanted = {
        Path(str(value)).name.casefold()
        for value in names
        if str(value or "").strip()
    }
    matches: list[Path] = []
    if name_index is None:
        for root in roots:
            for name in wanted:
                matches.extend(path for path in root.rglob("*") if path.name.casefold() == name)
    else:
        for name in wanted:
            matches.extend(Path(path) for path in name_index.get(name, ()))
    unique = list(
        dict.fromkeys(
            resolved
            for path in matches
            for resolved in (path.resolve(),)
            if any(resolved == root or resolved.is_relative_to(root) for root in roots)
        )
    )
    if len(unique) == 1:
        return unique[0], "unique_name"
    if unique:
        return None, "ambiguous"
    return None, "outside_roots" if outside_roots else "missing"


def _same_review(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    ignored = {"updated_utc", "review_revision", "authored_by", "site"}
    return {key: value for key, value in first.items() if key not in ignored} == {
        key: value for key, value in second.items() if key not in ignored
    }


def _publish(
    *,
    sidecar: Path,
    payload: dict[str, Any],
    write: bool,
) -> tuple[str, str]:
    if sidecar.exists():
        try:
            existing = load_review(sidecar)
        except Exception as exc:
            return "existing_invalid", str(exc)
        if _same_review(existing, payload):
            return "already_identical", ""
        return "existing_conflict", "existing sidecar was preserved"
    if write:
        atomic_write_review(sidecar, payload)
        return "written", ""
    return "ready", ""


def _ca_review_status(review: Mapping[str, Any]) -> str:
    status = str(review.get("status") or "unreviewed")
    if status != "accepted_auto":
        return status
    manual_values = dict(review.get("manual_values_mA") or {})
    final_values = dict(review.get("final_values_mA") or review.get("values") or {})
    if manual_values and final_values:
        return "manual_adjusted"
    return status


def _apply_ca_review(draft: dict[str, Any], review: Mapping[str, Any]) -> None:
    target = draft["targets"][0]
    status = _ca_review_status(review)
    target["status"] = status
    target["included"] = status in {"accepted_auto", "manual_adjusted"}
    target["analysis_included"] = status in {
        "accepted_auto",
        "manual_adjusted",
        "no_transition",
    }
    target["auto_values"] = dict(review.get("auto_values_mA") or target.get("auto_values") or {})
    target["manual_values"] = dict(review.get("manual_values_mA") or {})
    target["final_values"] = (
        {}
        if status == "no_transition"
        else dict(review.get("final_values_mA") or review.get("values") or {})
    )
    target["cleared_labels"] = list(review.get("cleared_labels") or ())
    draft["updated_utc"] = str(review.get("updated_at") or utc_now_text())


def _stress_from_label(label: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", label)
    return float(match.group(0)) if match else None


def _apply_tma_reviews(draft: dict[str, Any], reviews: list[Mapping[str, Any]]) -> None:
    for review in reviews:
        stress = _stress_from_label(str(review.get("target_label") or ""))
        candidates = [
            target
            for target in draft["targets"]
            if stress is not None
            and isinstance(target.get("target"), Mapping)
            and abs(float(target["target"]["stress_mpa"]) - stress) <= 1e-6
        ]
        if len(candidates) != 1:
            continue
        target = candidates[0]
        old_status = str(review.get("status") or "")
        manual = dict(review.get("manual_values_mA") or {})
        status = (
            "manual_adjusted"
            if old_status == "accepted" and (manual or review.get("cleared_labels"))
            else "accepted_auto"
            if old_status == "accepted"
            else old_status
        )
        target["status"] = status or "unreviewed"
        target["included"] = status in {"accepted_auto", "manual_adjusted"}
        target["analysis_included"] = target["status"] in {
            "accepted_auto",
            "manual_adjusted",
            "no_transition",
        }
        target["auto_values"] = dict(review.get("auto_values_mA") or target.get("auto_values") or {})
        target["manual_values"] = manual
        target["final_values"] = (
            {} if target["status"] == "no_transition" else dict(review.get("values") or {})
        )
        target["cleared_labels"] = list(review.get("cleared_labels") or ())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="Disposable project copy to read.")
    parser.add_argument("--root", action="append", type=Path, default=[], help="Measurement search root.")
    parser.add_argument("--out", required=True, type=Path, help="Audit manifest directory.")
    parser.add_argument("--write", action="store_true", help="Atomically create missing sidecars.")
    args = parser.parse_args()

    project = load_project(args.project)
    sections = project.get("sections")
    if not isinstance(sections, Mapping):
        raise SystemExit("Project has no sections mapping.")
    roots = [path.resolve() for path in args.root]
    search_names: set[object] = set()
    annealing_section = sections.get("annealing")
    if isinstance(annealing_section, Mapping):
        for review in _records(annealing_section, "transition_reviews").values():
            search_names.update((review.get("graph_label"), review.get("source_path")))
    tma_section = sections.get("mini_dma")
    if isinstance(tma_section, Mapping):
        for review in _records(tma_section, "mini_dma_transition_reviews").values():
            search_names.update((review.get("source_name"), review.get("record_path")))
    name_index = _build_name_index(roots, search_names)
    rows: list[dict[str, Any]] = []

    annealing = sections.get("annealing")
    if isinstance(annealing, Mapping):
        for record_id, review in _records(annealing, "transition_reviews").items():
            source, match = _candidate(
                review.get("source_path"),
                (review.get("graph_label"), review.get("source_path")),
                roots,
                name_index=name_index,
            )
            row = {
                "family": "current_annealing",
                "record_id": record_id,
                "requested_path": str(review.get("source_path") or ""),
                "stored_status": str(review.get("status") or "unreviewed"),
                "status": _ca_review_status(review),
                "match": match,
            }
            if source is None:
                row["result"] = match
                rows.append(row)
                continue
            try:
                draft = current_annealing_review_draft(source)
                _apply_ca_review(draft, review)
                sidecar = sidecar_path_for_measurement(source, family="current_annealing")
                result, detail = _publish(sidecar=sidecar, payload=draft, write=args.write)
                row.update(source=str(source), sidecar=str(sidecar), result=result, detail=detail)
            except Exception as exc:
                row.update(source=str(source), result="invalid_measurement", detail=str(exc))
            rows.append(row)

    tma = sections.get("mini_dma")
    if isinstance(tma, Mapping):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for _record_id, review in _records(tma, "mini_dma_transition_reviews").items():
            grouped[str(review.get("record_path") or "")].append(review)
        for record_path, reviews in grouped.items():
            source, match = _candidate(
                record_path,
                [review.get("source_name") for review in reviews],
                roots,
                name_index=name_index,
            )
            status_counts = Counter(
                str(review.get("status") or "unreviewed") for review in reviews
            )
            row = {
                "family": "tma",
                "record_path": record_path,
                "target_count": len(reviews),
                "status_counts": dict(sorted(status_counts.items())),
                "match": match,
            }
            if source is None:
                row["result"] = match
                rows.append(row)
                continue
            run_dir = source if source.is_dir() else source.parent
            try:
                draft = tma_review_draft(run_dir)
                _apply_tma_reviews(draft, reviews)
                sidecar = sidecar_path_for_measurement(run_dir, family="tma")
                result, detail = _publish(sidecar=sidecar, payload=draft, write=args.write)
                row.update(source=str(run_dir), sidecar=str(sidecar), result=result, detail=detail)
            except Exception as exc:
                row.update(source=str(run_dir), result="invalid_measurement", detail=str(exc))
            rows.append(row)

    args.out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "transition_review_backfill_manifest_v1",
        "created_utc": utc_now_text(),
        "mode": "write" if args.write else "dry_run",
        "project": str(args.project.resolve()),
        "roots": [str(root) for root in roots],
        "counts": {
            key: sum(1 for row in rows if row.get("result") == key)
            for key in sorted({str(row.get("result")) for row in rows})
        },
        "records": rows,
    }
    target = args.out / "transition_review_backfill_manifest.json"
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {target}")
    print(json.dumps(manifest["counts"], sort_keys=True))
    return 0 if not any(row.get("result") in {"existing_conflict", "ambiguous"} for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
