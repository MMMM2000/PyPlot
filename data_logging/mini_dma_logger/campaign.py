from __future__ import annotations

import csv
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class CampaignError(ValueError):
    """Raised when a TMA campaign manifest is malformed."""


def _parse_scalar(text: str) -> Any:
    value = text.strip()
    if not value:
        return ""
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        if "." in value:
            result = float(value)
            return result if math.isfinite(result) else value
        return int(value)
    except ValueError:
        return value


def _minimal_yaml_load(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    pending_key: tuple[int, dict[str, Any], str] | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        content = raw_line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if pending_key is not None and indent > pending_key[0]:
            _pending_indent, pending_parent, pending_name = pending_key
            if content.startswith("- "):
                container: list[Any] = []
            else:
                container = {}
            pending_parent[pending_name] = container
            stack.append((indent - 1, container))
            parent = container
            pending_key = None
        if content.startswith("- "):
            if not isinstance(parent, list):
                raise CampaignError(f"List item has no list parent: {raw_line}")
            item_text = content[2:].strip()
            if ":" in item_text:
                key, value = item_text.split(":", 1)
                item: dict[str, Any] = {}
                parent.append(item)
                if value.strip():
                    item[key.strip()] = _parse_scalar(value)
                else:
                    item[key.strip()] = {}
                    pending_key = (indent, item, key.strip())
                stack.append((indent, item))
            else:
                parent.append(_parse_scalar(item_text))
            continue
        if ":" not in content:
            raise CampaignError(f"Expected key/value line: {raw_line}")
        if not isinstance(parent, dict):
            raise CampaignError(f"Mapping item has no mapping parent: {raw_line}")
        key, value = content.split(":", 1)
        name = key.strip()
        if value.strip():
            parent[name] = _parse_scalar(value)
            pending_key = None
        else:
            parent[name] = {}
            pending_key = (indent, parent, name)
    return root


def load_campaign(path: Path | str) -> dict[str, Any]:
    manifest_path = Path(path)
    text = manifest_path.read_text(encoding="utf-8-sig")
    if manifest_path.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        payload = _minimal_yaml_load(text)
    if not isinstance(payload, dict):
        raise CampaignError("Campaign manifest must be a mapping")
    return payload


def nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def campaign_root(manifest: Mapping[str, Any], manifest_path: Path | str) -> Path:
    root = nested(manifest, "campaign", "root")
    if isinstance(root, str) and root:
        return Path(root)
    return Path(manifest_path).resolve().parent


def _as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _nested_dotted(mapping: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = mapping
    for key in dotted_path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _contract_values_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    expected_number = _as_float(expected)
    actual_number = _as_float(actual)
    if expected_number is not None and actual_number is not None:
        return math.isclose(actual_number, expected_number, rel_tol=0.0, abs_tol=1e-9)
    return actual == expected


def _git(args: Sequence[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=(completed.stdout + completed.stderr),
        )
    # Git can emit non-fatal filesystem warnings on stderr while porcelain
    # stdout remains clean. Those warnings must not be mistaken for changes.
    return completed.stdout.strip()


@dataclass(frozen=True)
class CampaignCheckResult:
    manifest_path: str
    campaign_id: str
    campaign_root: str
    ok: bool
    errors: list[str]
    warnings: list[str]
    derived: dict[str, Any]


def validate_campaign(
    manifest_path: Path | str,
    *,
    repo_root: Path | str | None = None,
    skip_git: bool = False,
) -> CampaignCheckResult:
    path = Path(manifest_path)
    manifest = load_campaign(path)
    errors: list[str] = []
    warnings: list[str] = []
    required_paths = [
        ("kind",),
        ("campaign", "id"),
        ("campaign", "root"),
        ("objective", "primary"),
        ("sample", "composition"),
        ("sample", "microwire"),
        ("sample", "length_mm"),
        ("sample", "diameter_mm"),
        ("control_source", "required_base_ref"),
        ("control_source", "approved_control_logic_version"),
        ("hardware", "current_channel"),
        ("hardware", "current_voltage_limit_v"),
        ("safety", "max_stress_mpa"),
        ("run_plan", "stages"),
        ("reporting", "report_path"),
    ]
    for keys in required_paths:
        if nested(manifest, *keys) in (None, ""):
            errors.append("missing required field: " + ".".join(keys))
    if manifest.get("kind") != "mini_dma_optimization_campaign":
        errors.append("kind must be mini_dma_optimization_campaign")
    length = _as_float(nested(manifest, "sample", "length_mm"))
    travel_fraction = _as_float(nested(manifest, "safety", "max_correction_travel_fraction"))
    travel_limit_mm = None
    if length is not None and travel_fraction is not None:
        travel_limit_mm = length * travel_fraction
        if travel_fraction < 0.01 or travel_fraction > 0.25:
            warnings.append("max_correction_travel_fraction is outside the expected 0.01-0.25 range")
    root = campaign_root(manifest, path)
    recipe_contracts: list[dict[str, Any]] = []
    stages = nested(manifest, "run_plan", "stages")
    if not isinstance(stages, list) or not stages:
        errors.append("run_plan.stages must be a non-empty list")
    else:
        for index, stage in enumerate(stages):
            if not isinstance(stage, Mapping):
                errors.append(f"run_plan.stages[{index}] must be a mapping")
                continue
            if not stage.get("id"):
                errors.append(f"run_plan.stages[{index}].id is required")
            if not stage.get("recipe_path"):
                errors.append(f"run_plan.stages[{index}].recipe_path is required")
                continue
            expected_recipe = stage.get("expected_recipe")
            if expected_recipe is None:
                continue
            if not isinstance(expected_recipe, Mapping) or not expected_recipe:
                errors.append(f"run_plan.stages[{index}].expected_recipe must be a non-empty mapping")
                continue
            recipe_path = root / str(stage["recipe_path"])
            contract_result = {"stage": stage.get("id"), "recipe_path": str(recipe_path), "ok": False}
            recipe_contracts.append(contract_result)
            if not recipe_path.is_file():
                errors.append(f"run_plan.stages[{index}] recipe does not exist: {recipe_path}")
                continue
            try:
                recipe_payload = json.loads(recipe_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"run_plan.stages[{index}] recipe could not be read: {exc}")
                continue
            if not isinstance(recipe_payload, Mapping):
                errors.append(f"run_plan.stages[{index}] recipe must be a mapping")
                continue
            mismatches: list[str] = []
            for dotted_path, expected in expected_recipe.items():
                actual = _nested_dotted(recipe_payload, str(dotted_path))
                if not _contract_values_match(actual, expected):
                    mismatches.append(f"{dotted_path}: expected {expected!r}, got {actual!r}")
            if mismatches:
                errors.append(
                    f"run_plan.stages[{index}] recipe contract mismatch: " + "; ".join(mismatches)
                )
                contract_result["mismatches"] = mismatches
            else:
                contract_result["ok"] = True
    report_path = nested(manifest, "reporting", "report_path")
    if isinstance(report_path, str) and report_path:
        report_full = root / report_path
    else:
        report_full = None
    git_info: dict[str, Any] = {}
    if not skip_git:
        cwd = Path(repo_root) if repo_root is not None else Path.cwd()
        try:
            branch = _git(["branch", "--show-current"], cwd)
            commit = _git(["rev-parse", "HEAD"], cwd)
            status = _git(["status", "--porcelain"], cwd)
            git_info.update({"branch": branch, "commit": commit, "dirty": bool(status)})
            approved_version = str(
                nested(manifest, "control_source", "approved_control_logic_version") or ""
            ).strip()
            if approved_version and not commit.startswith(approved_version):
                errors.append(
                    "current commit does not match control_source.approved_control_logic_version "
                    f"({commit} vs {approved_version})"
                )
            prefix = nested(manifest, "control_source", "required_branch_prefix")
            if prefix and branch and not branch.startswith(str(prefix)) and branch != "main":
                errors.append(f"current branch {branch!r} does not match required prefix {prefix!r}")
            if nested(manifest, "control_source", "require_clean_git") is True and status:
                errors.append("git worktree is not clean")
            base_ref = nested(manifest, "control_source", "required_base_ref")
            if nested(manifest, "control_source", "require_up_to_date_with_base") is True and base_ref:
                try:
                    behind = _git(["rev-list", "--count", f"HEAD..{base_ref}"], cwd)
                    git_info["commits_behind_base"] = int(behind or "0")
                    if int(behind or "0") > 0:
                        errors.append(f"HEAD is {behind} commit(s) behind {base_ref}")
                except subprocess.CalledProcessError as exc:
                    warnings.append(f"could not compare HEAD to {base_ref}: {exc.output.strip()}")
        except subprocess.CalledProcessError as exc:
            errors.append(f"git check failed: {exc.output.strip()}")
    derived = {
        "max_correction_travel_mm": travel_limit_mm,
        "report_path": None if report_full is None else str(report_full),
        "recipe_contracts": recipe_contracts,
        "git": git_info,
    }
    return CampaignCheckResult(
        manifest_path=str(path),
        campaign_id=str(nested(manifest, "campaign", "id") or ""),
        campaign_root=str(root),
        ok=not errors,
        errors=errors,
        warnings=warnings,
        derived=derived,
    )


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def discover_campaign_run_dirs(manifest: Mapping[str, Any], manifest_path: Path | str) -> list[Path]:
    root = campaign_root(manifest, manifest_path)
    paths: list[Path] = []
    explicit = manifest.get("runs")
    if isinstance(explicit, list):
        for item in explicit:
            if isinstance(item, str):
                paths.append(Path(item))
            elif isinstance(item, Mapping) and isinstance(item.get("path"), str):
                paths.append(Path(str(item["path"])))
    raw_runs = root / "raw_runs"
    if raw_runs.exists():
        for child in raw_runs.iterdir():
            if child.is_dir() and (child / "metadata.json").exists():
                paths.append(child)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path if path.is_absolute() else root / path
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def check_result_to_json(result: CampaignCheckResult) -> str:
    return json.dumps(asdict(result), indent=2, ensure_ascii=False)
