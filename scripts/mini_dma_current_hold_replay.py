"""Replay COPIES of raw scale recordings through real filter/hold methods.

Usage: uv run python scripts/mini_dma_current_hold_replay.py COPIED_RUN NEW_OUTPUT
The three UTC-bin scenarios are sensitivity assumptions, not exact reconstruction.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_logging.mini_dma_logger.current_hold_replay import (  # noqa: E402
    CurrentHoldReplay, ReplayConfig, PRODUCTION_METHODS, app,
)


def utc_seconds(value: str) -> float:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    previous = -math.inf
    for row in rows:
        now = utc_seconds(row["timestamp_utc"])
        if now < previous:
            raise ValueError(f"UTC moves backwards in {path.name}; explicit clock repair required")
        row["utc_s"] = now
        previous = now
    return rows


def clock_audit(rows):
    offsets = [r["utc_s"] - float(r["elapsed_s"]) for r in rows]
    return {"rows": len(rows), "elapsed_resets": sum(
        float(b["elapsed_s"]) < float(a["elapsed_s"]) for a, b in zip(rows, rows[1:])),
        "utc_minus_elapsed_min_s": min(offsets), "utc_minus_elapsed_max_s": max(offsets)}


def config_from_metadata(meta):
    sweep = meta["controlled_current_sweep"]
    scale = meta["scale"]
    profile = app._force_control_profile_for_scale_settings(
        int(scale["baud"]), scale["request_command"], scale["line_ending"])
    readability = app._scale_readability_g_for_settings(
        int(scale["baud"]), scale["request_command"], scale["line_ending"])
    if readability is None:
        raise ValueError("unknown scale readability; do not silently guess")
    keys = ("filter_window_s", "noise_sigma", "min_pause_stress_mpa",
            "min_resume_stress_mpa", "pause_factor", "resume_factor", "resume_stable_s")
    config = ReplayConfig(
        diameter_mm=float(meta["wire_diameter_mm"]),
        initial_length_mm=float(meta["initial_length_mm"]),
        force_control_profile=profile, scale_readability_g=readability,
        scale_interval_ms=int(scale["poll_interval_ms"]),
        control_interval_ms=int(meta["control"]["control_interval_ms"]),
        **{"current_sweep_hold_" + k: float(sweep["current_ramp_hold_" + k]) for k in keys})
    if meta.get("calibration", {}).get("report"):
        raise ValueError("calibration report requires an explicit adapter; refusing unknown calibration state")
    return config


def validate_overrides(meta):
    overrides = meta.get("runtime_overrides", []) + meta["controlled_current_sweep"].get("runtime_overrides", [])
    audited = []
    for override in overrides:
        changes = override.get("changes")
        if not changes:
            raise ValueError("runtime override lacks a before/after audit")
        fields = set()
        for change in changes:
            old, new = change["old"], change["new"]
            fields.update(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
        if fields - {"target_value", "target_start_value", "target_end_value"}:
            raise ValueError("non-target runtime changes require segment-specific configuration")
        audited.append({"timestamp_utc": override["timestamp_utc"], "changed_fields": sorted(fields)})
    return audited


def replay(raw, controls, measurements, meta, *, offset_s, duration_s, log_delay_s=0.0,
           sample_indexed=False):
    """Causal UTC join: raw upper timestamp <= assumed decision instant.

    Raw timestamps are truncated to ms; control/measurement timestamps to seconds.
    No nearest-neighbor join, future interpolation, or cross-stream elapsed join.
    log_delay_s is a declared assumed logging latency, NOT measured latency.
    """
    if sample_indexed:
        if log_delay_s:
            raise ValueError("logging-delay sensitivity applies only to UTC-bin alignment")
        if any(int(r.get("sample_index", "0")) != i+1 for i, r in enumerate(raw)):
            raise ValueError("sample-index alignment requires a complete contiguous raw sidecar")
    validate_overrides(meta)
    first = next(r for r in controls if r["automation_phase"] in {"current", "current_hold"})
    beginning = first["utc_s"]
    end = beginning + duration_s
    origin = min(raw[0]["utc_s"], controls[0]["utc_s"], beginning - log_delay_s) - 1.0
    host = CurrentHoldReplay(config_from_metadata(meta))
    # Recorded completion/settling evidence is absent: never assume an idle motor
    # for the long-window resume. Exact fake-motor paths are exercised synthetically.
    host._pending_motion_command = "unknown_recorded_motor_state"
    sweep = meta["controlled_current_sweep"]
    selected = [r for r in controls if beginning <= r["utc_s"] <= end]
    raw_cursor = bisect_right([r["utc_s"] for r in raw], beginning - 60.0 - log_delay_s)
    measurement_cursor = 0
    latest_current = None
    previous_observed_current = None
    active_key = None
    previous_elapsed = None
    previous_holding = False
    rows = []
    stop_reason = "requested_window_end"
    step_start = 0.0
    indexed_origins = []
    arrival_rounding_clamps = []
    previous_count = 0
    for event in selected:
        decision_utc = event["utc_s"] + offset_s - log_delay_s
        count = None
        if sample_indexed:
            count = int(event["raw_scale_sample_count"])
            age = float(event["latest_scale_age_s"])
            if count < max(1, previous_count) or count > len(raw) or not math.isfinite(age) or age < 0:
                raise ValueError("invalid indexed control observation")
            previous_count = count
            decision_utc = raw[count-1]["utc_s"] + .001 + age
            if not event["utc_s"] - .005 <= decision_utc <= event["utc_s"] + 1.005:
                raise ValueError("sample-index clock disagrees with control UTC bin")
            indexed_origins.append(decision_utc - float(event["elapsed_s"]))
        now = decision_utc - origin
        while raw_cursor < len(raw) and (
            raw_cursor < count if sample_indexed else raw[raw_cursor]["utc_s"] + .001 <= decision_utc
        ):
            sample = raw[raw_cursor]
            sample_s = sample["utc_s"] - origin
            arrival_s = sample["utc_s"] + .001 - origin
            # Two independently truncated ms endpoints can overlap slightly.
            # Delay this arrival within that uncertainty; never move a decision back.
            if sample_indexed and arrival_s < host.clock.now_s:
                overlap = host.clock.now_s - arrival_s
                if overlap > .002:
                    raise ValueError("indexed sample arrival overlaps a prior decision beyond timestamp precision")
                arrival_rounding_clamps.append(overlap)
                arrival_s = host.clock.now_s
            host.deliver_scale(sample_s=sample_s, arrival_s=arrival_s,
                               applied_load_g=float(sample["applied_load_g"]))
            raw_cursor += 1
        # Only measurements whose ENTIRE timestamp bin precedes this decision.
        while (measurement_cursor < len(measurements)
               and measurements[measurement_cursor]["utc_s"] + 1.0 <= decision_utc):
            current_text = measurements[measurement_cursor].get("current_set_mA", "")
            if current_text:
                latest_current = float(current_text)
            measurement_cursor += 1
        if (previous_observed_current is not None and latest_current is not None
                and latest_current < previous_observed_current - 1e-6):
            stop_reason = "first_observed_current_reversal_upward_leg_only"
            break
        previous_observed_current = latest_current
        elapsed = float(event["elapsed_s"])
        reset = previous_elapsed is not None and elapsed < previous_elapsed
        previous_elapsed = elapsed
        phase = event["automation_phase"]
        if phase not in {"current", "current_hold"} or reset:
            active_key = None
            previous_holding = False
            continue
        if not event["automation_basis"] or not event["automation_target_value"]:
            continue  # Device/logging events are not additional hold-controller ticks.
        if event["automation_basis"] != "stress_mpa":
            raise ValueError("this recording adapter supports iso-stress only")
        key = (event["plateau_index"], float(event["automation_target_value"]))
        if key != active_key:
            host.start_step(target_mpa=key[1], now_s=now,
                            start_mA=float(sweep["current_start_mA"]),
                            end_mA=float(sweep.get("first_overheating_current_end_mA", sweep["current_end_mA"])
                                         if sweep.get("first_overheating") and key[1] == sweep.get("first_overheating_target_mpa")
                                         else sweep["current_end_mA"]),
                            rate_mA_s=float(sweep["current_ramp_rate_mA_s"]),
                            hold_enabled=bool(sweep["current_ramp_hold_on_error"]))
            active_key = key
            step_start = now
            previous_holding = False
        if event.get("current_hold_bypass_active"):
            host._current_hold_bypass_active = event["current_hold_bypass_active"] == "1"
        result = host.tick(now)
        result.pop("current_mA")  # Shadow decisions do not predict a counterfactual PSU/plant.
        for field, source in (("noise_mpa", "filtered_noise"), ("slope_mpa_s", "filtered_slope_per_s")):
            observed = event.get(source)
            result[field + "_minus_recorded"] = (
                result[field] - float(observed) if observed and result[field] is not None else None)
        result.update(recorded_elapsed_s=elapsed, recorded_phase=phase,
                      target_mpa=key[1], source_timestamp_utc=event["timestamp_utc"],
                      recorded_current_mA=latest_current,
                      cold_start=now-step_start < 20.0,
                      transition=("pause" if result["holding"] else "resume")
                      if result["holding"] != previous_holding else "")
        previous_holding = result["holding"]
        rows.append(result)
    warm = [r for r in rows if not r["cold_start"]]
    def mae(field):
        values = [abs(r[field]) for r in warm if r[field] is not None]
        return sum(values) / len(values) if values else None
    return rows, {"ticks": len(rows), "warm_ticks": len(warm), "stop_reason": stop_reason,
        "sample_indexed": sample_indexed,
        "arrival_rounding_clamp_count": len(arrival_rounding_clamps),
        "arrival_rounding_clamp_max_s": max(arrival_rounding_clamps, default=0.0),
        "indexed_clock_origin_spread_s": max(indexed_origins) - min(indexed_origins) if indexed_origins else None,
        "filtered_noise_mae_mpa": mae("noise_mpa_minus_recorded"),
        "filtered_slope_mae_mpa_s": mae("slope_mpa_s_minus_recorded"),
        "pause_transitions": sum(r["transition"] == "pause" for r in rows),
        "resume_transitions": sum(r["transition"] == "resume" for r in rows),
        "warm_hold_tick_fraction": sum(r["holding"] for r in warm) / len(warm) if warm else None,
        "warm_historical_phase_agreement": sum(
            r["holding"] == (r["recorded_phase"] == "current_hold") for r in warm
        ) / len(warm) if warm else None,
        "latest_sample_never_future": all(
            r["latest_sample_s"] is None or r["latest_sample_s"] <= r["time_s"] for r in rows)}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("copied_run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration-s", type=float, default=180.0)
    parser.add_argument("--assumed-log-delay-s", type=float, default=0.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration_s) or args.duration_s <= 0:
        parser.error("duration must be positive and finite")
    if not math.isfinite(args.assumed_log_delay_s) or args.assumed_log_delay_s < 0:
        parser.error("assumed logging delay must be finite and nonnegative")
    if args.output.exists():
        parser.error("output must be a new directory; refusing overwrite")
    run = args.copied_run.resolve()
    meta = json.loads((run / "metadata.json").read_text(encoding="utf-8-sig"))
    raw = read_csv(run / "scale_raw.csv")
    controls = read_csv(run / "control_trace.csv")
    measurements = read_csv(run / "measurement.csv")
    config = config_from_metadata(meta)
    # Final metadata may reflect later edits. Do not apply it silently to earlier data.
    overrides = validate_overrides(meta)
    args.output.mkdir(parents=True, exist_ok=False)
    results = {}
    scenarios = [("early", 0.0), ("midpoint", .5), ("late", .999)]
    if (not args.assumed_log_delay_s and raw[0].get("sample_index")
            and controls[0].get("raw_scale_sample_count")):
        scenarios.append(("sample_indexed", 0.0))
    for name, offset in scenarios:
        rows, results[name] = replay(raw, controls, measurements, meta, offset_s=offset,
                                    duration_s=args.duration_s,
                                    log_delay_s=args.assumed_log_delay_s,
                                    sample_indexed=name == "sample_indexed")
        if not rows:
            raise ValueError("no eligible current-sweep ticks")
        with (args.output / (name + ".csv")).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    config_dict = asdict(config)
    config_dict["force_control_profile"] = config.force_control_profile.value
    report = {
        "scope": "real filter + hold subsystem shadow replay; NOT a closed-loop plant validation",
        "source": str(run), "duration_requested_s": args.duration_s,
        "assumed_logging_delay_s": args.assumed_log_delay_s,
        "input_sha256": {name: sha256(run / name) for name in
                         ("metadata.json", "scale_raw.csv", "control_trace.csv", "measurement.csv")},
        "code_sha256": {"application": sha256(Path(app.__file__)),
                        "adapter": sha256(Path(sys.modules[CurrentHoldReplay.__module__].__file__)),
                        "runner": sha256(Path(__file__))},
        "production_methods": list(PRODUCTION_METHODS), "config": config_dict,
        "runtime_override_audit": overrides,
        "clock_audit": {"raw": clock_audit(raw), "controls": clock_audit(controls),
                        "measurements": clock_audit(measurements)},
        "assumptions": [
            "UTC rather than independently reset elapsed counters joins streams.",
            "Early/midpoint/late use positions within each control event's one-second UTC bin.",
            "Raw sample availability is its millisecond UTC upper bound; actual delivery latency is unknown.",
            "Control logging latency is unknown: scenarios are not guaranteed bounds on decision time.",
            "When available, sample-indexed replay uses the logged raw counter and latest sample age, with UTC-bin consistency checks.",
            "Counter/age fields are not an atomic decision snapshot: indexed replay reduces but does not eliminate acquisition races.",
            "Indexed arrival overlaps up to 2 ms are delayed to the preceding tick and counted; larger inconsistencies fail closed.",
            "No interpolation, smoothing with future data, or inferred counterfactual wire response.",
            "Recorded control events are irregular observation ticks, not a complete control-loop clock.",
            "Applied-load input bypasses historical tare conversion; calibration noise is unknown (None).",
            "Initial upward current leg only; stop on first causally observed reversal.",
            "Motor completion is unknown, so cycle-center motor-idle eligibility is conservatively vetoed.",
            "Twenty seconds after each step start are flagged cold-start; historical state is not inferred.",
            "Historical phase agreement is descriptive, not an accuracy or safety score.",
        ], "results": results}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
