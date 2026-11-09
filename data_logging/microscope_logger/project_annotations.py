"""Reviewed, backed-up specimen annotations for normal Builder packages.

Preparing an annotation only writes a separate candidate and its review record.
Publication is explicit and uses the repository's guarded publication script.
No microscope observation is inferred from the currently selected specimen.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping
from uuid import uuid4

from .samples import _sha256, _wire_display


@dataclass(frozen=True)
class BrittleUpdateReview:
    source_path: str
    source_sha256: str
    candidate_path: str
    candidate_sha256: str
    composition: str
    microwire: str
    brittle: bool
    changes: tuple[dict[str, Any], ...]
    review_path: str
    verification: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _matches(row: Mapping[str, Any], composition: str, microwire: str) -> bool:
    if str(row.get("Composition") or "").strip() != composition:
        return False
    raw_wire = row.get("Microwire")
    alias = row.get("Wire")
    primary_text, alias_text = _wire_display(raw_wire), _wire_display(alias)
    if (primary_text and alias_text and primary_text != alias_text
            and microwire in (primary_text, alias_text)):
        raise ValueError("The selected specimen has conflicting Microwire/Wire identities in a project row.")
    if raw_wire is None or not str(raw_wire).strip():
        raw_wire = alias
    return _wire_display(raw_wire) == microwire


def _write_new_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def _table(section: Mapping[str, Any]) -> dict[str, list]:
    result = {key: section.get(key) for key in ("columns", "rows", "index")}
    if (not all(isinstance(value, list) for value in result.values())
            or len(result["rows"]) != len(result["index"])
            or not all(isinstance(row, dict) for row in result["rows"])):
        raise ValueError("The Builder section does not contain a supported table.")
    return {"columns": list(result["columns"]),
            "rows": [dict(row) for row in result["rows"]],
            "index": list(result["index"])}


def _verify_preserved_entries(source_index: Any, candidate_index: Any,
                              changed_sections: set[str]) -> dict[str, Any]:
    """Verify every original entry except the intentionally replaced tables."""
    replaced = {source_index.sections[key]["table"] for key in changed_sections
                if key in source_index.sections}
    for name, descriptor in source_index.entries.items():
        if name not in replaced and candidate_index.entries.get(name) != descriptor:
            raise ValueError(f"Annotation changed an unrelated package entry: {name}")
    additions = set(candidate_index.entries) - set(source_index.entries)
    permitted = set()
    for key in changed_sections - set(source_index.sections):
        descriptor = candidate_index.sections[key]
        permitted.update((descriptor["state"], descriptor["table"]))
    if additions != permitted or set(source_index.entries) - set(candidate_index.entries):
        raise ValueError("Annotation changed the unrelated package entry inventory.")
    if (source_index.manifest["project_id"] != candidate_index.manifest["project_id"]
            or candidate_index.manifest["revision"] <= source_index.manifest["revision"]):
        raise ValueError("Annotation did not preserve project identity and advance revision.")
    return {"all_package_entries_verified": True,
            "unrelated_entries_preserved": len(source_index.entries) - len(replaced),
            "changed_sections": sorted(changed_sections),
            "project_id": candidate_index.manifest["project_id"],
            "revision": candidate_index.manifest["revision"]}


def _python_executable() -> str:
    if getattr(sys, "frozen", False):
        raise ValueError("Reviewed Builder writeback currently requires the Python/uv version of PyPlot. "
                         "A frozen application cannot start the isolated verification and publication workers.")
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return str(executable)


def _verify_with_builder(candidate: Path, review_dir: Path, *, composition: str,
                         microwire: str, brittle: bool) -> dict[str, Any]:
    request = review_dir / "verification-request.json"
    output = review_dir / "verification.json"
    isolated = review_dir / "verification-environment"
    isolated.mkdir()
    _write_new_json(request, {"candidate": str(candidate), "composition": composition,
                             "microwire": microwire, "brittle": brittle,
                             "output": str(output), "isolated_root": str(isolated)})
    env = os.environ.copy()
    env.update({"QT_QPA_PLATFORM": "offscreen", "MPLBACKEND": "Agg",
                "MICROWIRE_BUILDER_STORAGE_ROOT": str(isolated / "builder-storage"),
                "MICROWIRE_BUILDER_SETTINGS_FILE": str(isolated / "builder.ini"),
                "MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS": "1",
                "MPLCONFIGDIR": str(isolated / "matplotlib"),
                "TMP": str(isolated), "TEMP": str(isolated), "TMPDIR": str(isolated)})
    root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(root)
    command = [_python_executable(), "-c",
               "from data_logging.microscope_logger.project_annotations import _verify_in_isolated_process; "
               "import sys; _verify_in_isolated_process(sys.argv[1])", str(request)]
    stdout_path = review_dir / "verification-stdout.txt"
    stderr_path = review_dir / "verification-stderr.txt"
    try:
        with stdout_path.open("x", encoding="utf-8") as stdout_stream, stderr_path.open("x", encoding="utf-8") as stderr_stream:
            completed = subprocess.run(command, cwd=root, env=env, stdout=stdout_stream, stderr=stderr_stream,
                                       timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Isolated Builder/logger verification exceeded five minutes. "
                         f"The source was not changed; diagnostics are in {review_dir}.") from exc
    if completed.returncode or not output.exists():
        raise ValueError("The candidate failed isolated Builder/logger verification. "
                         f"Diagnostics are in {review_dir}.")
    return json.loads(output.read_text(encoding="utf-8"))


def _verify_in_isolated_process(request_path: str) -> None:
    """Private subprocess entry point: never reuse the application's live stores."""
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    from PyQt6 import QtCore, QtWidgets
    root = Path(request["isolated_root"])
    for scope in (QtCore.QSettings.Scope.UserScope, QtCore.QSettings.Scope.SystemScope):
        QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat, scope, str(root / "qt"))
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
    from microwire_data_builder import ui
    from microwire_data_builder.core import BRITTLE_COLUMN, BRITTLE_OVERRIDE_COLUMN
    from data_logging.mini_dma_logger.mini_dma_logger import _load_builder_project_table_projection
    from .samples import load_project_samples

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ui.BuilderWindow()
    window._auto_open_last = False
    try:
        candidate = Path(request["candidate"])
        window.load_project_synchronously_for_automation(candidate)
        frame = window.microscope_section.data.table
        matches = [row for row in frame.to_dict(orient="records")
                   if _matches(row, request["composition"], request["microwire"])]
        if not matches:
            raise ValueError("Builder did not restore the annotated microscope specimen.")
        for row in matches:
            if (row.get(BRITTLE_OVERRIDE_COLUMN) is not request["brittle"]
                    or (str(row.get(BRITTLE_COLUMN) or "").strip().lower() == "brittle")
                    != request["brittle"]):
                raise ValueError("Builder did not preserve the explicit Brittle annotation.")
        logger_payload = _load_builder_project_table_projection(candidate)
        logger_rows = logger_payload["sections"]["microscope"]["rows"]
        if not any(_matches(row, request["composition"], request["microwire"])
                   and row.get(BRITTLE_OVERRIDE_COLUMN) is request["brittle"]
                   for row in logger_rows):
            raise ValueError("The normal TMA logger table import lost the annotation.")
        if not any(choice.composition == request["composition"]
                   and choice.microwire == request["microwire"]
                   for choice in load_project_samples(candidate)):
            raise ValueError("The microscope logger cannot reload the annotated sample.")
        _write_new_json(Path(request["output"]), {
            "builder_opened_in_isolated_storage": True, "logger_table_import_passed": True,
            "explicit_brittle_survived_builder_open": True,
            "candidate_sha256": _sha256(candidate), "matched_microscope_rows": len(matches)})
    finally:
        window._dirty = False
        window.close()
        app.processEvents()


def prepare_brittle_update(
    source: str | Path, workdir: str | Path, *, composition: str, microwire: str,
    brittle: bool, expected_sha256: str,
) -> BrittleUpdateReview:
    """Prepare an inspectable new package without modifying the source project.

    Every exact specimen row in microscope/assemble is listed in the review.
    Numeric separators are equivalent; treatment/end suffixes stay distinct.
    A known specimen without microscope history gains an annotation-only row.
    """
    from microwire_data_builder import project_package as pp
    from microwire_data_builder.core import BRITTLE_COLUMN, BRITTLE_OVERRIDE_COLUMN
    from microwire_data_builder.project_maintenance import update_project_tables

    _python_executable()  # Fail before preparing a candidate in unsupported frozen builds.
    if type(brittle) is not bool:
        raise ValueError("Brittle must be an explicit true or false observation.")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("A lowercase expected source SHA-256 is required.")
    composition, microwire = str(composition).strip(), _wire_display(microwire)
    if not composition or not microwire:
        raise ValueError("Select the exact composition and microwire before annotating.")
    source = Path(source).expanduser().resolve(strict=True)
    if _sha256(source) != expected_sha256:
        raise ValueError("The Builder project changed. Reload the sample catalogue.")
    if not pp.is_project_package(source):
        raise ValueError("Brittle writeback requires a normal packaged Builder project; migrate legacy files separately.")
    index = pp.inspect_project_package(source, verify_entries=True)
    sections = {key: index.read_section(key, load_payloads=False) for key in index.sections}
    known = any(_matches(row, composition, microwire)
                for section in sections.values() for row in section.get("rows", [])
                if isinstance(row, Mapping))
    if not known:
        raise ValueError("The exact composition and microwire are not present in this project.")
    changes = []
    tables = {}
    for key in ("microscope", "assemble"):
        if key not in sections:
            continue
        table = _table(sections[key])
        for row_number, row in enumerate(table["rows"]):
            if not _matches(row, composition, microwire):
                continue
            change = {"section": key, "row_index": row_number,
                      "table_index": table["index"][row_number], "composition": composition,
                      "microwire": microwire, "source_microwire": row.get("Microwire", row.get("Wire")),
                      "previous_brittle": row.get(BRITTLE_COLUMN),
                      "brittle": "brittle" if brittle else None}
            row[BRITTLE_COLUMN] = change["brittle"]
            if BRITTLE_COLUMN not in table["columns"]:
                table["columns"].append(BRITTLE_COLUMN)
            if key == "microscope":
                change["previous_override"] = row.get(BRITTLE_OVERRIDE_COLUMN)
                change["override"] = brittle
                row[BRITTLE_OVERRIDE_COLUMN] = brittle
                if BRITTLE_OVERRIDE_COLUMN not in table["columns"]:
                    table["columns"].append(BRITTLE_OVERRIDE_COLUMN)
            changes.append(change)
            tables[key] = table
    if "microscope" not in tables:
        from microwire_data_builder.ui import MICROSCOPE_TABLE_COLUMNS
        from microwire_data_builder.core import _microwire_key_to_str, _microwire_parts_from_label
        parts = _microwire_parts_from_label(microwire)
        if parts is None:
            raise ValueError("This specimen label needs an existing microscope row before it can be annotated.")
        table = (_table(sections["microscope"]) if "microscope" in sections
                 else {"columns": [], "rows": [], "index": []})
        table["columns"] = list(dict.fromkeys([*table["columns"], *MICROSCOPE_TABLE_COLUMNS]))
        row = {column: None for column in table["columns"]}
        row.update({"Composition": composition, "Microwire": microwire,
                    "_key": _microwire_key_to_str((composition, *parts)),
                    BRITTLE_COLUMN: "brittle" if brittle else None,
                    BRITTLE_OVERRIDE_COLUMN: brittle})
        label = f"microscope-annotation-{uuid4().hex}"
        table["rows"].append(row)
        table["index"].append(label)
        changes.append({"section": "microscope", "row_index": len(table["rows"]) - 1,
                        "table_index": label, "composition": composition, "microwire": microwire,
                        "inserted_row": True, "previous_brittle": None,
                        "brittle": row[BRITTLE_COLUMN], "previous_override": None, "override": brittle})
        tables["microscope"] = table
    if _sha256(source) != expected_sha256:
        raise ValueError("The Builder project changed while preparing the annotation.")
    workdir = Path(workdir).expanduser().resolve()
    review_dir = workdir / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12])
    review_dir.mkdir(parents=True)
    candidate = review_dir / "candidate.pydpj"
    if set(tables).issubset(index.sections):
        candidate_index = update_project_tables(source, candidate, tables)
    else:
        updated_sections = {}
        for key, table in tables.items():
            section = dict(sections.get(key, {"section": key, "title": "Microscope",
                                              "extra": {}, "sources": [], "processed": {}}))
            section.update(table)
            updated_sections[key] = section
        candidate_index = pp.write_project_package(
            candidate, {"kind": pp.PROJECT_KIND, "sections": updated_sections,
                        "writer": "Microscope specimen annotation"},
            source_index=index, loaded_sections=set(updated_sections), replace_existing=False)
    candidate_index = pp.inspect_project_package(candidate, verify_entries=True)
    verification = _verify_preserved_entries(index, candidate_index, set(tables))
    verification.update(_verify_with_builder(candidate, review_dir, composition=composition,
                                             microwire=microwire, brittle=brittle))
    if _sha256(source) != expected_sha256:
        raise ValueError("The Builder project changed during candidate verification. Reload it.")
    review = BrittleUpdateReview(str(source), expected_sha256, str(candidate), _sha256(candidate),
                                composition, microwire, brittle, tuple(changes),
                                str(review_dir / "review.json"), verification)
    _write_new_json(Path(review.review_path), review.to_dict())
    return review


def publish_brittle_update(review: BrittleUpdateReview, *, backup: str | Path,
                           receipt_path: str | Path) -> dict[str, Any]:
    """Publish an unchanged reviewed candidate with a new backup and receipt."""
    from microwire_data_builder import project_package as pp

    executable = _python_executable()
    source, candidate = Path(review.source_path), Path(review.candidate_path)
    backup, receipt_path = Path(backup).expanduser().resolve(), Path(receipt_path).expanduser().resolve()
    if len({path.resolve() for path in (source, candidate, backup, receipt_path)}) != 4:
        raise ValueError("Project, candidate, backup and receipt must use different paths.")
    stdout_path = receipt_path.with_name(receipt_path.name + ".stdout.txt")
    stderr_path = receipt_path.with_name(receipt_path.name + ".stderr.txt")
    if any(path.exists() for path in (backup, receipt_path, stdout_path, stderr_path)):
        raise FileExistsError("Choose new, unused backup and receipt paths.")
    if _sha256(source) != review.source_sha256:
        raise ValueError("The Builder project changed after review. Prepare the annotation again.")
    if _sha256(candidate) != review.candidate_sha256:
        raise ValueError("The candidate changed after review. Prepare the annotation again.")
    recorded = json.loads(Path(review.review_path).read_text(encoding="utf-8"))
    if recorded != json.loads(json.dumps(review.to_dict())):
        raise ValueError("The review record changed. Prepare and review the annotation again.")
    required = ("all_package_entries_verified", "builder_opened_in_isolated_storage",
                "logger_table_import_passed", "explicit_brittle_survived_builder_open")
    if (not all(review.verification.get(key) is True for key in required)
            or review.verification.get("candidate_sha256") != review.candidate_sha256):
        raise ValueError("This candidate has not passed the required isolated verification.")
    original = pp.inspect_project_package(source, verify_entries=True)
    prepared = pp.inspect_project_package(candidate, verify_entries=True)
    selected_rows = [row for row in prepared.read_section("microscope", load_payloads=False)["rows"]
                     if _matches(row, review.composition, review.microwire)]
    if (not selected_rows or any(row.get("_brittle_override") is not review.brittle
                                or row.get("Brittle") != ("brittle" if review.brittle else None)
                                for row in selected_rows)):
        raise ValueError("The candidate does not contain the reviewed specimen and Brittle observation.")
    _verify_preserved_entries(original, prepared, set(review.verification["changed_sections"]))
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the receipt before changing the project. A failed publication keeps
    # a durable failure record; a successful one keeps the script's exact receipt.
    with receipt_path.open("x", encoding="utf-8") as receipt_stream:
        def save_receipt(payload: dict[str, Any]) -> None:
            receipt_stream.seek(0)
            receipt_stream.truncate()
            json.dump(payload, receipt_stream, indent=2, ensure_ascii=False)
            receipt_stream.write("\n")
            receipt_stream.flush()
            os.fsync(receipt_stream.fileno())

        pending = {"publication_pending": True, "source": str(source), "backup": str(backup),
                   "candidate": str(candidate), "expected_source_sha256": review.source_sha256,
                   "candidate_sha256": review.candidate_sha256, "stdout_path": str(stdout_path),
                   "stderr_path": str(stderr_path), "review_path": review.review_path}
        save_receipt(pending)
        command = [executable, str(Path(__file__).resolve().parents[2] / "scripts" / "publish_builder_project.py"),
                   str(candidate), str(source), "--backup", str(backup),
                   "--expected-target-sha256", review.source_sha256]
        try:
            with stdout_path.open("x", encoding="utf-8") as stdout_stream, stderr_path.open("x", encoding="utf-8") as stderr_stream:
                completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2],
                                           stdout=stdout_stream, stderr=stderr_stream, timeout=300,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (subprocess.TimeoutExpired, OSError) as exc:
            failure = {**pending, "publication_pending": False, "publication_failed": True,
                       "publication_result_uncertain": isinstance(exc, subprocess.TimeoutExpired),
                       "error": str(exc), "source_sha256_after_failure": _sha256(source) if source.exists() else None,
                       "backup_sha256_after_failure": _sha256(backup) if backup.exists() else None}
            save_receipt(failure)
            raise ValueError(f"Guarded publication did not finish normally. Inspect {receipt_path} and its diagnostics before retrying.") from exc
        if completed.returncode:
            failure = {**pending, "publication_pending": False, "publication_failed": True,
                       "returncode": completed.returncode}
            save_receipt(failure)
            raise ValueError(f"Guarded Builder publication failed; see {receipt_path}.")
        try:
            receipt = json.loads(stdout_path.read_text(encoding="utf-8"))
            if (not isinstance(receipt, dict) or receipt.get("published_sha256") != review.candidate_sha256
                    or receipt.get("previous_sha256") != review.source_sha256
                    or receipt.get("strict_verification_passed") is not True):
                raise ValueError("Publication receipt does not match the reviewed candidate.")
        except (ValueError, OSError) as exc:
            save_receipt({**pending, "publication_pending": False, "publication_result_uncertain": True,
                          "error": "Publication returned no valid receipt; inspect the preserved output and hashes."})
            raise ValueError(f"Publication returned an invalid receipt; inspect {receipt_path} before retrying.") from exc
        receipt["annotation"] = {"composition": review.composition, "microwire": review.microwire,
                                 "brittle": review.brittle, "review_path": review.review_path}
        save_receipt(receipt)
    return receipt


__all__ = ["BrittleUpdateReview", "prepare_brittle_update", "publish_brittle_update"]
