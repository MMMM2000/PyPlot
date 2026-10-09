from __future__ import annotations

import json
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


JOB_SCHEMA_VERSION = 1
MICROWIRE_WORD_JOB_TYPE = "microwire_word_export"


class JobRequestError(ValueError):
    """Raised when a job request cannot be interpreted safely."""


@dataclass(frozen=True)
class JobPaths:
    status: Path
    progress: Path
    manifest: Path
    log: Path
    cancel: Path


@dataclass(frozen=True)
class MicrowireWordJobRequest:
    job_id: str
    source: Path
    output_dir: Path | None
    sample: str | None
    include_origin: bool
    force_project_rebuild: bool
    graphs_only: bool
    dry_run: bool
    paths: JobPaths


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_object(payload: object, *, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise JobRequestError(f"{label} must be a JSON object.")
    return payload


def _as_bool(value: object, *, default: bool, field_name: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise JobRequestError(f"Job field '{field_name}' must be true or false.")


def _as_optional_string(value: object, *, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    raise JobRequestError(f"Job field '{field_name}' must be a string when provided.")


def _resolve_path(value: object, *, base_dir: Path, field_name: str, required: bool = False) -> Path | None:
    text = _as_optional_string(value, field_name=field_name)
    if text is None:
        if required:
            raise JobRequestError(f"Job field '{field_name}' is required.")
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def _default_job_id(job_path: Path) -> str:
    stem = job_path.stem.strip() or "microwire_word_job"
    return stem.replace(" ", "_")


def default_job_paths(job_path: Path, *, job_id: str) -> JobPaths:
    base = job_path.parent / "job_runs" / job_id
    return JobPaths(
        status=base / "status.json",
        progress=base / "progress.json",
        manifest=base / "manifest.json",
        log=base / "job.log",
        cancel=base / "cancel.requested",
    )


def _job_paths_from_payload(payload: Mapping[str, object], *, job_path: Path, job_id: str) -> JobPaths:
    defaults = default_job_paths(job_path, job_id=job_id)
    paths_payload = payload.get("paths")
    if paths_payload is None:
        paths = {}
    else:
        paths = _as_object(paths_payload, label="Job field 'paths'")
    base_dir = job_path.parent
    return JobPaths(
        status=_resolve_path(paths.get("status"), base_dir=base_dir, field_name="paths.status") or defaults.status,
        progress=_resolve_path(paths.get("progress"), base_dir=base_dir, field_name="paths.progress") or defaults.progress,
        manifest=_resolve_path(paths.get("manifest"), base_dir=base_dir, field_name="paths.manifest") or defaults.manifest,
        log=_resolve_path(paths.get("log"), base_dir=base_dir, field_name="paths.log") or defaults.log,
        cancel=_resolve_path(paths.get("cancel"), base_dir=base_dir, field_name="paths.cancel") or defaults.cancel,
    )


def load_microwire_word_job_request(job_path: Path | str) -> MicrowireWordJobRequest:
    path = Path(job_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise JobRequestError(f"Job file is not valid JSON: {exc}") from exc
    payload = _as_object(payload, label="Job file")
    version = payload.get("version")
    if version != JOB_SCHEMA_VERSION:
        raise JobRequestError(
            f"Unsupported job version {version!r}. Only version {JOB_SCHEMA_VERSION} is supported."
        )
    job_type = payload.get("job_type")
    if job_type != MICROWIRE_WORD_JOB_TYPE:
        raise JobRequestError(
            f"Unsupported job_type {job_type!r}. Expected {MICROWIRE_WORD_JOB_TYPE!r}."
        )
    base_dir = path.parent
    job_id = str(payload.get("job_id") or _default_job_id(path)).strip()
    if not job_id:
        raise JobRequestError("Job field 'job_id' must not be empty.")
    source = _resolve_path(payload.get("source"), base_dir=base_dir, field_name="source", required=True)
    assert source is not None
    output_dir = _resolve_path(payload.get("output_dir"), base_dir=base_dir, field_name="output_dir")
    paths = _job_paths_from_payload(payload, job_path=path, job_id=job_id)
    return MicrowireWordJobRequest(
        job_id=job_id,
        source=source,
        output_dir=output_dir,
        sample=_as_optional_string(payload.get("sample"), field_name="sample"),
        include_origin=_as_bool(payload.get("include_origin"), default=True, field_name="include_origin"),
        force_project_rebuild=_as_bool(
            payload.get("force_project_rebuild"),
            default=False,
            field_name="force_project_rebuild",
        ),
        graphs_only=_as_bool(payload.get("graphs_only"), default=False, field_name="graphs_only"),
        dry_run=_as_bool(payload.get("dry_run"), default=False, field_name="dry_run"),
        paths=paths,
    )


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_status(
    request: MicrowireWordJobRequest,
    *,
    state: str,
    message: str,
    step: str | None = None,
    exit_code: int | None = None,
    error: Mapping[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "kind": "pyplot_job_status",
        "version": JOB_SCHEMA_VERSION,
        "job_type": MICROWIRE_WORD_JOB_TYPE,
        "job_id": request.job_id,
        "state": state,
        "message": message,
        "updated_at": utc_now_iso(),
        "pid": os.getpid(),
        "source": str(request.source),
        "output_dir": str(request.output_dir) if request.output_dir is not None else None,
        "dry_run": request.dry_run,
    }
    if step:
        payload["step"] = step
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if error is not None:
        payload["error"] = dict(error)
    write_json(request.paths.status, payload)


def append_progress(
    request: MicrowireWordJobRequest,
    *,
    event: str,
    message: str,
    step: str | None = None,
    fraction: float | None = None,
) -> None:
    path = request.paths.progress
    events: list[dict[str, Any]]
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("events"), list):
                events = [event for event in loaded["events"] if isinstance(event, dict)]
            elif isinstance(loaded, list):
                events = [event for event in loaded if isinstance(event, dict)]
            else:
                events = []
        except Exception:
            events = []
    else:
        events = []
    entry: dict[str, Any] = {
        "time": utc_now_iso(),
        "event": event,
        "message": message,
    }
    if step:
        entry["step"] = step
    if fraction is not None:
        entry["fraction"] = max(0.0, min(1.0, float(fraction)))
    events.append(entry)
    write_json(path, {"events": events})


def write_manifest(
    request: MicrowireWordJobRequest,
    *,
    state: str,
    exit_code: int,
    command: list[str],
) -> None:
    write_json(
        request.paths.manifest,
        {
            "kind": "pyplot_job_manifest",
            "version": JOB_SCHEMA_VERSION,
            "job_type": MICROWIRE_WORD_JOB_TYPE,
            "job_id": request.job_id,
            "state": state,
            "exit_code": exit_code,
            "created_at": utc_now_iso(),
            "source": str(request.source),
            "output_dir": str(request.output_dir) if request.output_dir is not None else None,
            "sample": request.sample,
            "include_origin": request.include_origin,
            "force_project_rebuild": request.force_project_rebuild,
            "graphs_only": request.graphs_only,
            "dry_run": request.dry_run,
            "status_path": str(request.paths.status),
            "progress_path": str(request.paths.progress),
            "log_path": str(request.paths.log),
            "cancel_path": str(request.paths.cancel),
            "equivalent_command": command,
        },
    )


def error_payload(exc: BaseException, *, user_message: str | None = None) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "user_message": user_message or str(exc),
        "traceback": traceback.format_exc(),
    }


def microwire_word_command(request: MicrowireWordJobRequest) -> list[str]:
    command = ["python", "launcher.py", "--microwire-word-report", str(request.source)]
    if request.output_dir is not None:
        command.extend(["--out", str(request.output_dir)])
    if request.sample:
        command.extend(["--microwire-word-sample", request.sample])
    if request.force_project_rebuild:
        command.append("--microwire-word-force-project-rebuild")
    command.append("--microwire-word-origin" if request.include_origin else "--no-microwire-word-origin")
    if request.graphs_only:
        command.append("--microwire-word-graphs-only")
    return command
