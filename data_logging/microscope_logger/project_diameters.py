"""Reviewed microscope diameters, embedded evidence and guarded Builder saves."""
from __future__ import annotations

import copy
import csv
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .measurements import Calibration, _png_size, _validated_record, measure_line
from .project_annotations import _matches, _python_executable, _table, _write_new_json
from .samples import _sha256, _wire_display


HISTORY_KEY = "microscope_diameter_history"
EVIDENCE_PREFIX = "microscope_diameter_evidence_"
_COLUMNS = {"core": "d (µm)", "outer": "D (µm)"}
_OVERRIDES = {"core": "d", "outer": "D"}


@dataclass(frozen=True)
class DiameterUpdateReview:
    source_path: str
    source_sha256: str
    candidate_path: str
    candidate_sha256: str
    composition: str
    microwire: str
    session_directory: str
    measurement_ids: tuple[int, ...]
    summaries: dict[str, Any]
    changes: tuple[dict[str, Any], ...]
    evidence_inventory: dict[str, str | None]
    review_path: str
    verification: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_csv(data: bytes, records: Sequence[Mapping[str, Any]]) -> None:
    """Accept the original capture CSV schema and its review-column extension."""
    from .session_revisions import _csv_text
    try:
        actual = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))
        expected = csv.DictReader(io.StringIO(_csv_text(records), newline=""))
        fields, current = actual.fieldnames, expected.fieldnames
        optional = {"review_status", "reviewed_utc", "brittle", "brittle_observed_utc"}
        if (not fields or not current or len(fields) != len(set(fields))
                or not set(fields).issubset(current) or not (set(current) - optional).issubset(fields)):
            raise ValueError("Capture CSV uses an unsupported or incomplete measurement schema.")
        rows, expected_rows = list(actual), list(expected)
        if len(rows) != len(expected_rows) or any(
            row != {field: expected_row[field] for field in fields}
            for row, expected_row in zip(rows, expected_rows)
        ):
            raise ValueError("Capture CSV does not match its immutable measurement manifest.")
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("Capture CSV cannot be decoded using the supported schema.") from exc


def _session_snapshot(directory: Path) -> tuple[dict, dict[str, bytes], dict[str, str | None]]:
    """Read complete normal capture evidence without following arbitrary paths."""
    from .session_revisions import _read_base, _read_latest

    base, base_bytes, size, _references = _read_base(directory)
    pointer = _read_latest(directory, base, _digest(base_bytes))
    files = {"session.json": base_bytes, "image.png": (directory / "image.png").read_bytes()}
    manifests = {"session.json": base}
    revisions = directory / "revisions"
    if revisions.exists():
        for path in sorted(revisions.iterdir()):
            if path.is_dir() and re.fullmatch(r"[0-9a-f]{32}", path.name):
                if len(manifests) > 256:
                    raise ValueError("The capture has too many revisions for one reviewed import.")
                relative = f"revisions/{path.name}/session.json"
                raw = (directory / relative).read_bytes()
                payload = json.loads(raw)
                if (payload.get("application") != "PyPlot microscope" or payload.get("schema_version") != 1
                        or payload.get("session_id") != base["session_id"]
                        or payload.get("revision_id") != path.name
                        or payload.get("base_manifest_sha256") != _digest(base_bytes)):
                    raise ValueError("A capture revision is not bound to its original session.")
                files[relative], manifests[relative] = raw, payload
    current_name = "session.json"
    if pointer is not None:
        files["latest.json"] = pointer
        current_name = json.loads(pointer)["manifest"]
        if current_name not in manifests:
            raise ValueError("The latest capture revision is missing.")
    for name, payload in manifests.items():
        image = payload.get("image", {})
        if (image.get("sha256") != base["image"]["sha256"]
                or (image.get("width"), image.get("height")) != size):
            raise ValueError("A revision identifies a different original image.")
        records = payload.get("measurements")
        if not isinstance(records, list):
            raise ValueError("Capture measurements are invalid.")
        for record in records:
            _validated_record(record, size)
        csv_name = str(Path(name).with_name("measurements.csv")).replace("\\", "/")
        csv_bytes = (directory / csv_name).read_bytes()
        _validate_csv(csv_bytes, records)
        files[csv_name] = csv_bytes
        for reference in payload.get("reference_images", []):
            sha = reference.get("sha256")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise ValueError("Calibration reference inventory is invalid.")
            reference_name = f"reference_{sha}.png"
            raw = (directory / reference_name).read_bytes()
            if _digest(raw) != sha or _png_size(raw) != (reference.get("width"), reference.get("height")):
                raise ValueError("Calibration reference image failed its integrity check.")
            files[reference_name] = raw
    if sum(map(len, files.values())) > 256 * 1024 * 1024:
        raise ValueError("The capture evidence exceeds the 256 MiB import limit.")
    inventory = {name: _digest(raw) for name, raw in files.items()}
    inventory.setdefault("latest.json", None)
    return manifests[current_name], files, inventory


def _assert_session_unchanged(directory: Path, inventory: Mapping[str, str | None]) -> None:
    for name, sha in inventory.items():
        path = directory / name
        if (sha is None and path.exists()) or (sha is not None and (not path.is_file() or _sha256(path) != sha)):
            raise ValueError("The capture evidence changed after review. Prepare the diameter update again.")


def _verify_calibration(record: Mapping[str, Any], files: Mapping[str, bytes]) -> None:
    calibration = Calibration(**record["calibration"])
    evidence = record.get("calibration_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("A selected diameter lacks retained calibration evidence.")
    reference = files.get(f"reference_{evidence.get('image_sha256')}.png")
    if reference is None or _digest(reference) != evidence.get("image_sha256"):
        raise ValueError("A selected diameter's calibration reference PNG is missing or changed.")
    dimensions = _png_size(reference)
    if dimensions != calibration.image_size:
        raise ValueError("Calibration reference image size does not match the measured camera image.")
    distance = measure_line(evidence["start"], evidence["end"], image_size=dimensions,
                            objective=calibration.objective)["width_px"]
    if not math.isclose(distance, calibration.reference_pixels, rel_tol=1e-12):
        raise ValueError("Calibration reference geometry does not match its scale.")
    if (not str(record.get("camera_id", "")).strip()
            and evidence.get("image_sha256") != _digest(files["image.png"])):
        raise ValueError("An unknown camera requires a reference bound to the exact measured image; cross-image calibration is not allowed.")
    # The record's own acquisition/calibration snapshot is authoritative. Capture
    # revision metadata may describe a later UI selection and cannot relabel it.


def _positive_or_missing(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Existing diameter is not a positive numeric value.")
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("Existing diameter is not a positive numeric value.") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Existing diameter is not a positive numeric value.")
    return number


def _verify_preservation(original: Any, candidate: Any, sections: Mapping[str, dict], payload_id: str) -> dict:
    allowed = set()
    for key in sections:
        if key in original.sections:
            allowed.add(original.sections[key]["table"])
            if key == "microscope":
                allowed.add(original.sections[key]["state"])
    for name, descriptor in original.entries.items():
        if name not in allowed and candidate.entries.get(name) != descriptor:
            raise ValueError(f"Diameter update changed an unrelated package entry: {name}")
    if set(original.entries) - set(candidate.entries):
        raise ValueError("Diameter update removed package entries.")
    if set(candidate.sections) != set(original.sections) | {"microscope"}:
        raise ValueError("Diameter update changed unrelated project sections.")
    added = set(candidate.entries) - set(original.entries)
    permitted = {candidate.sections["microscope"]["payloads"][payload_id]}
    if "microscope" not in original.sections:
        permitted.update((candidate.sections["microscope"]["table"], candidate.sections["microscope"]["state"]))
    for name in added - permitted:
        if candidate.entries[name].role != "blob":
            raise ValueError("Diameter update added an unexpected package entry.")
    if (original.manifest["project_id"] != candidate.manifest["project_id"]
            or original.manifest["revision"] >= candidate.manifest["revision"]):
        raise ValueError("Diameter update did not preserve project identity and advance its revision.")
    for key, expected in sections.items():
        actual = candidate.read_section(key, load_payloads=False)
        expected = {name: value for name, value in expected.items() if name != "payloads"}
        if actual != expected:
            raise ValueError(f"Saved diameter section did not match its reviewed state: {key}")
    return {"all_package_entries_verified": True, "unrelated_entries_preserved": len(original.entries) - len(allowed),
            "project_id": candidate.manifest["project_id"], "revision": candidate.manifest["revision"],
            "changed_sections": sorted(sections), "evidence_payload_id": payload_id}


def _verify_builder(candidate: Path, directory: Path, composition: str, microwire: str,
                    summaries: dict, payload_id: str) -> dict:
    isolated = directory / "verification-environment"
    isolated.mkdir()
    request = directory / "verification-request.json"
    output = directory / "verification.json"
    _write_new_json(request, dict(candidate=str(candidate), composition=composition, microwire=microwire,
                                  summaries=summaries, payload_id=payload_id, root=str(isolated), output=str(output)))
    env = os.environ.copy()
    env.update(QT_QPA_PLATFORM="offscreen", MPLBACKEND="Agg", TMP=str(isolated), TEMP=str(isolated), TMPDIR=str(isolated),
               MICROWIRE_BUILDER_STORAGE_ROOT=str(isolated / "storage"),
               MICROWIRE_BUILDER_SETTINGS_FILE=str(isolated / "builder.ini"),
               MICROWIRE_BUILDER_SUPPRESS_INFO_DIALOGS="1", MPLCONFIGDIR=str(isolated / "matplotlib"))
    root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(root)
    command = [_python_executable(), "-c", "from data_logging.microscope_logger.project_diameters import _verify_isolated; import sys; _verify_isolated(sys.argv[1])", str(request)]
    try:
        with (directory / "verification-stdout.txt").open("x", encoding="utf-8") as out, (directory / "verification-stderr.txt").open("x", encoding="utf-8") as err:
            completed = subprocess.run(command, cwd=root, env=env, stdout=out, stderr=err, timeout=300,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Diameter verification timed out; source is unchanged. See {directory}.") from exc
    if completed.returncode or not output.is_file():
        raise ValueError(f"The diameter candidate failed isolated Builder/logger verification. See {directory}.")
    return json.loads(output.read_text(encoding="utf-8"))


def _verify_isolated(request_path: str) -> None:
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    from PyQt6 import QtCore, QtWidgets
    for scope in (QtCore.QSettings.Scope.UserScope, QtCore.QSettings.Scope.SystemScope):
        QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat, scope, str(Path(request["root"]) / "qt"))
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
    from microwire_data_builder import ui, project_package as pp
    from data_logging.mini_dma_logger.mini_dma_logger import _load_builder_project_table_projection
    from .samples import load_project_samples
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ui.BuilderWindow()
    def verify_numeric_rows(rows):
        for row in rows:
            for kind, column in _COLUMNS.items():
                summary = request["summaries"].get(kind)
                if summary is not None and not math.isclose(float(row[column]), summary["mean_um"], rel_tol=1e-12):
                    raise ValueError("The normal importer changed a selected reviewed diameter.")
            d, D = _positive_or_missing(row.get("d (µm)")), _positive_or_missing(row.get("D (µm)"))
            expected_ratio = round(d / D, 3) if d is not None and D is not None else None
            if d is not None and D is not None and d > D:
                raise ValueError("The normal importer restored an invalid core/outer diameter pair.")
            if row.get("d/D") != expected_ratio:
                raise ValueError("The normal importer changed the core/outer diameter ratio.")
    try:
        path = Path(request["candidate"])
        window.load_project_synchronously_for_automation(path)
        section = window.microscope_section
        rows = [row for row in section.data.table.to_dict(orient="records")
                if _matches(row, request["composition"], request["microwire"])]
        if not rows:
            raise ValueError("Normal Builder did not restore the measured specimen.")
        verify_numeric_rows(rows)
        for row in rows:
            key = row["_key"]
            for kind, column in _COLUMNS.items():
                summary = request["summaries"].get(kind)
                if summary is None:
                    continue
                expected = summary["mean_um"]
                if (not math.isclose(float(row[column]), expected, rel_tol=1e-12)
                        or section._overrides[key][_OVERRIDES[kind]] != expected
                        or section._validated[key].get(_OVERRIDES[kind] + "_reviewed") is not True):
                    raise ValueError("Normal Builder did not retain reviewed diameter overrides.")
        # Normal export/import must keep both the numeric override and its evidence binding.
        exported = section.export_project_payload()
        if request["payload_id"] not in exported.get("extra", {}).get("payloads", {}):
            raise ValueError("Normal Builder export lost the embedded diameter evidence binding.")
        section.import_project_payload(exported)
        verify_numeric_rows([row for row in section.data.table.to_dict(orient="records")
                             if _matches(row, request["composition"], request["microwire"])])
        history = section.data.extra.get(HISTORY_KEY, [])
        if not any(item.get("payload_id") == request["payload_id"] for item in history):
            raise ValueError("Normal Builder import lost diameter history.")
        logger = _load_builder_project_table_projection(path)
        logger_rows = [row for row in logger["sections"]["microscope"]["rows"]
                       if _matches(row, request["composition"], request["microwire"])]
        if not logger_rows:
            raise ValueError("Normal logger table import lost the measured specimen.")
        verify_numeric_rows(logger_rows)
        assembly_rows = [row for row in logger["sections"].get("assemble", {}).get("rows", [])
                         if _matches(row, request["composition"], request["microwire"])]
        verify_numeric_rows(assembly_rows)
        if not any(choice.composition == request["composition"] and choice.microwire == request["microwire"]
                   for choice in load_project_samples(path)):
            raise ValueError("The microscope sample catalogue lost the measured specimen.")
        index = pp.inspect_project_package(path, verify_entries=True)
        index.read_payload("microscope", request["payload_id"])
        _write_new_json(Path(request["output"]), dict(builder_opened_in_isolated_storage=True,
            logger_table_import_passed=True, diameter_override_roundtrip_passed=True,
            embedded_evidence_verified=True, candidate_sha256=_sha256(path)))
    finally:
        window._dirty = False
        window.close()
        app.processEvents()


def prepare_diameter_update(source: str | Path, workdir: str | Path, *, composition: str,
                             microwire: str, session_directory: str | Path,
                             measurement_ids: Sequence[int], expected_sha256: str) -> DiameterUpdateReview:
    """Prepare selected reviewed core/outer widths; never modify the source."""
    _python_executable()
    from microwire_data_builder import core, ui, project_package as pp, safe_codec
    from .diameter_review import summarize_reviewed_diameters, require_wire_level_diameters

    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha256)):
        raise ValueError("A lowercase expected target SHA-256 is required.")
    source = Path(source).expanduser().resolve(strict=True)
    if _sha256(source) != expected_sha256:
        raise ValueError("The Builder project changed. Reload its catalogue before preparing an update.")
    if not pp.is_project_package(source):
        raise ValueError("Diameter writeback requires a normal packaged Builder project.")
    composition, microwire = str(composition).strip(), _wire_display(microwire)
    session_directory = Path(session_directory).expanduser().resolve(strict=True)
    manifest, evidence_files, inventory = _session_snapshot(session_directory)
    summaries = summarize_reviewed_diameters(manifest["measurements"], composition=composition,
                                            microwire=microwire, selected_rows=measurement_ids)
    selected = [copy.deepcopy(manifest["measurements"][number - 1]) for number in summaries["selected_rows"]]
    require_wire_level_diameters(selected)
    index = pp.inspect_project_package(source, verify_entries=True)
    sections = {key: index.read_section(key, load_payloads=False) for key in index.sections}
    bindings = []
    for key, section in sections.items():
        bindings.extend(dict(section=key, row_index=number, composition=composition, microwire=microwire)
                        for number, row in enumerate(section.get("rows", [])) if _matches(row, composition, microwire))
    if not bindings:
        raise ValueError("The exact measured composition and microwire do not exist in this project.")
    source_snapshots = []
    for record in selected:
        sample = record["sample"]
        sample_bindings = [binding for binding in (sample, *sample.get("matching_sources", []))
                           if isinstance(binding, Mapping) and binding.get("source_kind") == "project"
                           and binding.get("source_path") and Path(binding["source_path"]).expanduser().resolve() == source
                           and str(binding.get("composition") or "").strip() == composition
                           and _wire_display(binding.get("microwire")) == microwire]
        if not sample_bindings:
            raise ValueError("A selected measurement belongs to a different Builder project path.")
        _verify_calibration(record, evidence_files)
        for snapshot in sample_bindings:
            old_sha = snapshot["source_sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", str(old_sha)):
                raise ValueError("A selected measurement has an invalid original project SHA-256.")
            if old_sha == expected_sha256:
                for binding in snapshot["source_rows"]:
                    matches = [item for item in bindings if item["section"] == binding["section"] and item["row_index"] == binding["row_index"]]
                    if not matches:
                        raise ValueError("A measurement's exact source row binding does not match this project snapshot.")
            source_snapshots.append(dict(source_sha256=old_sha, differs_from_current_target=old_sha != expected_sha256,
                                         source_rows=copy.deepcopy(snapshot["source_rows"])))
    parts = core._microwire_parts_from_label(microwire)
    if parts is None or _wire_display(core._microwire_label(*parts)) != microwire:
        raise ValueError("This specimen variant cannot be represented by an exact Builder override key.")
    key = core._microwire_key_to_str((composition, *parts))
    microscope = copy.deepcopy(sections.get("microscope", dict(section="microscope", title="Microscope",
        columns=list(ui.MICROSCOPE_TABLE_COLUMNS), rows=[], index=[], extra={}, sources=[], processed={})))
    table = _table(microscope)
    table["columns"] = list(dict.fromkeys([*table["columns"], *ui.MICROSCOPE_TABLE_COLUMNS]))
    matching_rows = [row for row in table["rows"] if _matches(row, composition, microwire)]
    if not matching_rows:
        row = {column: None for column in table["columns"]}
        row.update(Composition=composition, Microwire=microwire, _key=key, _images=[])
        table["rows"].append(row)
        table["index"].append("diameter-" + uuid4().hex)
        matching_rows = [row]
    extra = microscope.setdefault("extra", {})
    if not isinstance(extra, dict):
        raise ValueError("Microscope state extras are invalid.")
    overrides = extra.setdefault("overrides", {})
    validated = extra.setdefault("validated", {})
    if not isinstance(overrides, dict) or not isinstance(validated, dict):
        raise ValueError("Existing microscope override/review state is invalid.")
    override = overrides.setdefault(key, {})
    validation = validated.setdefault(key, {})
    if not isinstance(override, dict) or not isinstance(validation, dict):
        raise ValueError("Existing microscope override/review state is invalid.")
    existing = {}
    for kind, column in _COLUMNS.items():
        if summaries[kind] is not None:
            existing[kind] = summaries[kind]["mean_um"]
            continue
        values = [row.get(column) for row in matching_rows]
        values.append(override.get(_OVERRIDES[kind]))
        values.append(validation.get(_OVERRIDES[kind]))
        values.extend(row.get(column) for row in sections.get("assemble", {}).get("rows", [])
                      if _matches(row, composition, microwire))
        values = [value for value in map(_positive_or_missing, values) if value is not None]
        if values and any(not math.isclose(value, values[0], rel_tol=1e-10) for value in values[1:]):
            raise ValueError("Existing unselected diameter values disagree; review them before updating the other diameter.")
        existing[kind] = values[0] if values else None
    if existing["core"] is not None and existing["outer"] is not None and existing["core"] > existing["outer"]:
        raise ValueError("Metal core diameter cannot exceed the outer glass diameter, including the existing other value.")
    changes = []
    image_path = session_directory / "image.png"
    image_stat = image_path.stat()
    stamp = datetime.now(UTC).isoformat()
    for kind, column in _COLUMNS.items():
        summary = summaries[kind]
        if summary is None:
            continue
        value = summary["mean_um"]
        override[_OVERRIDES[kind]] = value
        validation.update({_OVERRIDES[kind]: value, _OVERRIDES[kind] + "_reviewed": True})
        for number, row in enumerate(table["rows"]):
            if _matches(row, composition, microwire):
                changes.append(dict(section="microscope", row_index=number, column=column, previous=row.get(column), value=value))
                row[column], row["_key"] = value, key
    validation["timestamp"] = stamp
    source_meta = dict(path=str(image_path), key=str(image_path.resolve()).lower(),
                       mtime=float(image_stat.st_mtime), size=image_stat.st_size)
    review_sources = validation.setdefault("sources", [])
    if not isinstance(review_sources, list):
        raise ValueError("Existing microscope review sources are invalid.")
    review_sources.append(source_meta)
    for row in matching_rows:
        images = row.get("_images")
        if images is not None and not isinstance(images, (list, tuple)):
            raise ValueError("Existing microscope image inventory is invalid; it was not replaced.")
        images = list(images) if images is not None else []
        if str(image_path) not in images:
            images.append(str(image_path))
        row["_images"] = images
        # Numeric measurement kind does not classify an image as glass-only or
        # exposed-core: those historical categories also drive Brittle inference.
        for kind, column in _COLUMNS.items():
            if summaries[kind] is None and existing[kind] is not None and row.get(column) in (None, ""):
                changes.append(dict(section="microscope", row_index=next(i for i, candidate_row in enumerate(table["rows"]) if candidate_row is row),
                    column=column, previous=row.get(column), value=existing[kind], preserves_existing_override=True))
                row[column] = existing[kind]
        d, D = _positive_or_missing(row.get("d (µm)")), _positive_or_missing(row.get("D (µm)"))
        row["d/D"] = round(d / D, 3) if d is not None and D is not None else None
    event_id = uuid4().hex
    payload_id = EVIDENCE_PREFIX + event_id
    history = extra.setdefault(HISTORY_KEY, [])
    if not isinstance(history, list):
        raise ValueError("Existing microscope diameter history is invalid.")
    original_extra = sections.get("microscope", {}).get("extra", {})
    history.append(dict(event_id=event_id, created_utc=stamp, session_id=manifest["session_id"],
        revision_id=manifest.get("revision_id"), composition=composition, microwire=microwire,
        measurement_ids=list(summaries["selected_rows"]), summaries=copy.deepcopy(summaries),
        selected_records=selected, evidence_inventory=inventory, original_source_snapshots=source_snapshots,
        target_source_sha256=expected_sha256, payload_id=payload_id,
        previous_state=dict(
            microscope_rows=[copy.deepcopy(row) for row in sections.get("microscope", {}).get("rows", [])
                             if _matches(row, composition, microwire)],
            assemble_rows=[copy.deepcopy(row) for row in sections.get("assemble", {}).get("rows", [])
                           if _matches(row, composition, microwire)],
            override=copy.deepcopy(original_extra.get("overrides", {}).get(key)),
            validated=copy.deepcopy(original_extra.get("validated", {}).get(key)))))
    payload_refs = extra.setdefault("payloads", {})
    if not isinstance(payload_refs, dict):
        raise ValueError("Existing microscope payload references are invalid.")
    payload_refs[payload_id] = payload_id
    microscope.update(table)
    updated = {"microscope": microscope}
    if "assemble" in sections:
        assembly = copy.deepcopy(sections["assemble"])
        for number, row in enumerate(assembly.get("rows", [])):
            if _matches(row, composition, microwire):
                for kind, column in _COLUMNS.items():
                    if summaries[kind] is not None:
                        value = summaries[kind]["mean_um"]
                        changes.append(dict(section="assemble", row_index=number, column=column, previous=row.get(column), value=value))
                        row[column] = value
                        if column not in assembly["columns"]:
                            assembly["columns"].append(column)
                    elif existing[kind] is not None and row.get(column) in (None, ""):
                        changes.append(dict(section="assemble", row_index=number, column=column,
                            previous=row.get(column), value=existing[kind], preserves_existing_override=True))
                        row[column] = existing[kind]
                        if column not in assembly["columns"]:
                            assembly["columns"].append(column)
                d, D = _positive_or_missing(row.get("d (µm)")), _positive_or_missing(row.get("D (µm)"))
                row["d/D"] = round(d / D, 3) if d is not None and D is not None else None
                if "d/D" not in assembly["columns"]:
                    assembly["columns"].append("d/D")
        updated["assemble"] = assembly
    history[-1]["changes"] = copy.deepcopy(changes)
    microscope["payloads"] = {payload_id: safe_codec.encode_envelope(dict(
        schema_version=1, application="PyPlot microscope diameter evidence", session_directory=str(session_directory),
        files={name: dict(sha256=_digest(raw), bytes=raw) for name, raw in evidence_files.items()},
        event=copy.deepcopy(history[-1])))}
    if _sha256(source) != expected_sha256:
        raise ValueError("The Builder project changed while preparing the diameter update.")
    _assert_session_unchanged(session_directory, inventory)
    directory = Path(workdir).expanduser().resolve() / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-diameters-" + event_id[:12])
    directory.mkdir(parents=True)
    candidate = directory / "candidate.pydpj"
    saved = pp.write_project_package(candidate, dict(kind=pp.PROJECT_KIND, sections=updated,
        writer="Reviewed microscope diameter import"), source_index=index, loaded_sections=set(updated), replace_existing=False)
    saved = pp.inspect_project_package(candidate, verify_entries=True)
    verification = _verify_preservation(index, saved, updated, payload_id)
    verification.update(_verify_builder(candidate, directory, composition, microwire, summaries, payload_id))
    if _sha256(source) != expected_sha256:
        raise ValueError("The Builder project changed during diameter verification.")
    _assert_session_unchanged(session_directory, inventory)
    review = DiameterUpdateReview(str(source), expected_sha256, str(candidate), _sha256(candidate), composition,
        microwire, str(session_directory), tuple(summaries["selected_rows"]), summaries, tuple(changes), inventory,
        str(directory / "review.json"), verification)
    _write_new_json(Path(review.review_path), review.to_dict())
    return review


def publish_diameter_update(review: DiameterUpdateReview, *, backup: str | Path,
                             receipt_path: str | Path) -> dict[str, Any]:
    """Publish a complete verified candidate through the guarded repository script."""
    from microwire_data_builder import project_package as pp
    executable = _python_executable()
    source, candidate = Path(review.source_path), Path(review.candidate_path)
    backup, receipt_path = Path(backup).resolve(), Path(receipt_path).resolve()
    stdout, stderr = receipt_path.with_name(receipt_path.name + ".stdout.txt"), receipt_path.with_name(receipt_path.name + ".stderr.txt")
    if len({path.resolve() for path in (source, candidate, backup, receipt_path, stdout, stderr)}) != 6:
        raise ValueError("Project, candidate, backup and receipt paths must be distinct.")
    if any(path.exists() for path in (backup, receipt_path, stdout, stderr)):
        raise FileExistsError("Choose new backup and receipt paths.")
    if _sha256(source) != review.source_sha256 or _sha256(candidate) != review.candidate_sha256:
        raise ValueError("The source or diameter candidate changed after review.")
    if json.loads(Path(review.review_path).read_text(encoding="utf-8")) != json.loads(json.dumps(review.to_dict())):
        raise ValueError("The diameter review record changed.")
    _assert_session_unchanged(Path(review.session_directory), review.evidence_inventory)
    required = ("all_package_entries_verified", "builder_opened_in_isolated_storage", "logger_table_import_passed",
                "diameter_override_roundtrip_passed", "embedded_evidence_verified")
    if not all(review.verification.get(field) is True for field in required) or review.verification.get("candidate_sha256") != review.candidate_sha256:
        raise ValueError("Diameter candidate has not passed complete isolated verification.")
    pp.inspect_project_package(candidate, verify_entries=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    pending = dict(publication_pending=True, source=str(source), backup=str(backup), candidate=str(candidate),
        expected_source_sha256=review.source_sha256, candidate_sha256=review.candidate_sha256,
        stdout_path=str(stdout), stderr_path=str(stderr), review_path=review.review_path)
    with receipt_path.open("x", encoding="utf-8") as stream:
        def save(payload):
            stream.seek(0)
            stream.truncate()
            json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        save(pending)
        command = [executable, str(Path(__file__).resolve().parents[2] / "scripts" / "publish_builder_project.py"),
                   str(candidate), str(source), "--backup", str(backup), "--expected-target-sha256", review.source_sha256,
                   "--expected-candidate-sha256", review.candidate_sha256]
        try:
            with stdout.open("x", encoding="utf-8") as out, stderr.open("x", encoding="utf-8") as err:
                result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], stdout=out, stderr=err,
                                        timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise ValueError("Guarded publication script failed.")
            receipt = json.loads(stdout.read_text(encoding="utf-8"))
            if (receipt.get("published_sha256") != review.candidate_sha256 or receipt.get("previous_sha256") != review.source_sha256
                    or receipt.get("strict_verification_passed") is not True):
                raise ValueError("Publication receipt does not match the reviewed candidate.")
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            save(dict(pending, publication_pending=False, publication_failed=True,
                      publication_result_uncertain=True, error=str(exc),
                      source_sha256_after_failure=_sha256(source) if source.exists() else None,
                      backup_sha256_after_failure=_sha256(backup) if backup.exists() else None))
            raise ValueError(f"Guarded diameter publication did not finish normally; inspect {receipt_path} before retrying.") from exc
        receipt["diameter_update"] = dict(composition=review.composition, microwire=review.microwire,
            summaries=review.summaries, measurement_ids=list(review.measurement_ids), review_path=review.review_path)
        save(receipt)
    return receipt


__all__ = ["DiameterUpdateReview", "prepare_diameter_update", "publish_diameter_update"]
