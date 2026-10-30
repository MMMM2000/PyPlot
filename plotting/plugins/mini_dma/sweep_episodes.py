"""Contiguous TMA recipe episodes and recorded endpoint strain metrics.

Keep target review identities separate from numerical episode selection. Raw
indices bind an existing target to native acquisition order before phase filters.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

SWEEP_PHASES = frozenset({"current", "current_hold", "current_limit_unwind"})
RAW = "_raw_row_index"


def _number(value: Any) -> float | None:
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _context(row: dict) -> tuple | None:
    target = _number(row.get("automation_target_value"))
    if target is None:
        return None
    return target, _number(row.get("plateau_index"))


def _commands(group: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(group.get("current_set_mA", pd.Series(index=group.index, dtype=float)), errors="coerce")


def _direction_info(group: pd.DataFrame) -> dict:
    command = _commands(group)
    if len(command) < 2:
        return dict(usable=False, range_usable=False, status="insufficient_recorded_points")
    if not np.isfinite(command).all():
        return dict(usable=False, range_usable=False, status="missing_or_nonfinite_commanded_current")
    span = float(command.max() - command.min())
    tolerance = max(0.02, span * 0.001)
    delta = command.diff().fillna(0)
    rising = delta > tolerance
    if not rising.any():
        return dict(usable=False, range_usable=False, status="no_recorded_rising_current_leg", command_span_mA=span)
    # Repeated sustained downward sequences, separated by real commanded rises,
    # cannot be represented as a single heat/cool cycle by choosing a global max.
    downward_runs = []
    steps, drop = 0, 0.0
    for d in delta:
        if d < -tolerance:
            steps += 1
            drop -= float(d)
        elif d > tolerance:
            if steps >= 4 and drop >= max(1.0, span * 0.05):
                downward_runs.append(dict(steps=steps, drop_mA=drop))
            steps, drop = 0, 0.0
    if steps >= 4 and drop >= max(1.0, span * 0.05):
        downward_runs.append(dict(steps=steps, drop_mA=drop))
    if len(downward_runs) > 1:
        return dict(usable=False, range_usable=True, status="ambiguous_multiple_current_cycles",
                    command_span_mA=span, current_tolerance_mA=tolerance, downward_runs=downward_runs)
    if "fatigue_cycle_index" in group:
        fatigue = pd.to_numeric(group["fatigue_cycle_index"], errors="coerce").dropna().unique()
        if len(fatigue) > 1:
            return dict(usable=False, range_usable=True, status="ambiguous_multiple_fatigue_cycles",
                        command_span_mA=span, current_tolerance_mA=tolerance,
                        fatigue_cycle_indices=[float(v) for v in fatigue])
    peak = int(command.to_numpy().argmax())
    maximum = float(command.max())
    # A flat commanded peak is exact recipe evidence. Small differences within
    # the recorded current resolution are tolerated only while contiguous.
    while peak + 1 < len(command) and abs(float(command.iloc[peak + 1]) - maximum) <= tolerance:
        peak += 1
    return dict(usable=True, range_usable=True, status="qualified_current_episode", command_span_mA=span,
                current_tolerance_mA=tolerance, turn_position=peak,
                rising_command_steps=int(rising.sum()), downward_runs=downward_runs)


def current_episodes(frame: pd.DataFrame) -> list[dict]:
    """Split the full timeline before filtering; do not join across load/setup.

    Each item has ``group`` (DataFrame), JSON-compatible ``audit`` and ``usable``.
    Singleton/hold-only islands are retained as rejected audit evidence.
    """
    if RAW not in frame:
        raise ValueError("Original acquisition _raw_row_index is required.")
    if not frame[RAW].is_unique:
        raise ValueError("Original acquisition _raw_row_index must be unique.")
    episodes = []
    positions = []
    previous_key = None
    previous_time = None

    def finish():
        if not positions:
            return
        group = frame.iloc[positions].copy()
        info = _direction_info(group)
        first, last = group.iloc[0], group.iloc[-1]
        audit = dict(
            episode_index=len(episodes), points=len(group),
            target_value=_number(first.get("automation_target_value")),
            plateau_index=_number(first.get("plateau_index")),
            start_raw_row=_number(first[RAW]), end_raw_row=_number(last[RAW]),
            start_elapsed_s=_number(first.get("elapsed_s")),
            end_elapsed_s=_number(last.get("elapsed_s")),
            phases=list(dict.fromkeys(str(v) for v in group["automation_phase"])),
            **info,
        )
        episodes.append(dict(group=group, audit=audit, usable=info["usable"], range_usable=info.get("range_usable", False)))

    context_columns = frame.reindex(columns=["elapsed_s", "automation_phase", "automation_target_value", "plateau_index"])
    for position, (raw_time, phase, target, plateau) in enumerate(context_columns.itertuples(index=False, name=None)):
        time = _number(raw_time)
        target = _number(target)
        key = (target, _number(plateau)) if target is not None else None
        valid = phase in SWEEP_PHASES and time is not None and key is not None
        boundary = not valid or key != previous_key or (previous_time is not None and time < previous_time)
        if boundary:
            finish()
            positions = []
        if valid:
            positions.append(position)
        previous_key = key if valid else None
        previous_time = time if valid else None
    finish()
    return episodes


def select_current_episode(
    frame: pd.DataFrame, old_group: pd.DataFrame, *, episodes: list[dict] | None = None,
) -> tuple[pd.DataFrame | None, dict]:
    """Bind an old export target to exactly one usable native timeline episode.

    The old group was deduplicated; overlap is sufficient and strict row-set
    containment would incorrectly discard legitimate identical hold samples.
    Multiple usable overlaps are reported as ambiguity; no longest/last choice.
    """
    if RAW not in old_group or old_group.empty:
        return None, dict(status="missing_old_group_raw_binding", selected=False)
    context_columns = old_group.reindex(columns=["automation_target_value", "plateau_index"])
    contexts = {(_number(target), _number(plateau)) for target, plateau in context_columns.itertuples(index=False, name=None)}
    contexts = {context for context in contexts if context[0] is not None}
    contexts.discard(None)
    if len(contexts) != 1:
        return None, dict(status="ambiguous_old_group_context", selected=False, context_count=len(contexts))
    context = next(iter(contexts))
    old_rows = set(old_group[RAW].tolist())
    matched = []
    rejected = []
    for episode in current_episodes(frame) if episodes is None else episodes:
        audit = episode["audit"]
        if (audit["target_value"], audit["plateau_index"]) != context:
            continue
        if not old_rows.intersection(episode["group"][RAW].tolist()):
            continue
        if episode["usable"] or episode["range_usable"]:
            matched.append(episode)
        else:
            rejected.append(audit)
    report = dict(selected=False, target_value=context[0], plateau_index=context[1],
                  usable_matching_episodes=[e["audit"] for e in matched], rejected_fragments=rejected)
    if len(matched) != 1:
        report["status"] = "ambiguous_multiple_usable_episodes" if matched else "no_qualified_current_episode"
        return None, report
    single_cycle = matched[0]["usable"]
    report.update(status="selected_unique_current_episode" if single_cycle else "selected_unique_multicycle_episode_range_only",
                  selected=True, usable_singlecycle=single_cycle, selected_episode=matched[0]["audit"])
    return matched[0]["group"].copy(), report


def cooling_completion(start_command_mA: float, end_command_mA: float,
                       median_cooling_decrement_mA: float | None,
                       cooling_recorded: bool) -> dict:
    """Qualify the same observed endpoint within one typical logged ramp step.

    A final command below the cold-start command also qualifies. The allowance
    uses an observed decrement, never a fraction of the maximum current. It
    does not borrow a later sample, interpolate length, or prove room temperature.
    This pure helper can qualify cached, unchanged native endpoint metrics.
    """
    start, end = _number(start_command_mA), _number(end_command_mA)
    step = _number(median_cooling_decrement_mA)
    valid = start is not None and end is not None and step is not None and step > 0
    epsilon = max(1e-9, max(abs(start or 0.0), abs(end or 0.0), abs(step or 0.0)) * 1e-12)
    return dict(
        cooling_complete=bool(cooling_recorded and valid and end <= start + step + epsilon),
        cooling_return_method="observed endpoint no higher than cold-start command plus one median recorded cooling decrement",
        cooling_median_recorded_decrement_mA=step,
        cooling_endpoint_above_cold_start_mA=end - start if start is not None and end is not None else None,
        cooling_completion_float_epsilon_mA=epsilon,
    )


def metrics_for_episode(
    group: pd.DataFrame, initial_length_mm: float | None = None,
    *, length_mm: pd.Series | None = None,
) -> dict:
    """Derive strain and highest recorded applied setpoint from one clean cycle.

    Public cooling/residual fields require a sustained descent and return to the
    cold-start command within one typical observed cooling decrement. Partial
    endpoint deltas remain explicitly private. Multiple cycles support range and
    reached setpoint only; no individual cycle is selected for endpoint changes.
    """
    info = _direction_info(group)
    result = dict(
        version=2, method="recipe_contiguous_commanded_turnaround_endpoint_difference",
        status=info["status"], points=len(group),
        max_strain_pct=None, heating_strain_pct=None, cooling_strain_pct=None,
        residual_strain_pct=None, max_current_mA=None,
        max_recorded_set_current_mA=None, max_recorded_measured_current_mA=None,
        heating_recorded=False, cooling_recorded=False, cooling_complete=False,
        partial_cooling_strain_pct=None, partial_residual_strain_pct=None,
        reference_basis="unavailable", reference_length_mm=None,
        verified_initial_length_mm=_number(initial_length_mm),
        endpoint="observed endpoint under the stated target; not unloaded permanent strain",
    )
    range_only = not info["usable"] and info.get("range_usable", False)
    if not info["usable"] and not range_only:
        return result
    command = _commands(group)
    turn = info.get("turn_position")
    tolerance = info["current_tolerance_mA"]
    start_command, end_command = map(float, (command.iloc[0], command.iloc[-1]))
    measured = pd.to_numeric(group["current_mA"], errors="coerce") if "current_mA" in group else pd.Series(index=group.index, dtype=float)
    result.update(max_current_mA=float(command.max()), max_recorded_set_current_mA=float(command.max()),
                  max_recorded_measured_current_mA=_number(measured.max()),
                  current_tolerance_mA=tolerance, turn_position=turn,
                  start_current_set_mA=start_command,
                  nominal_target_value=_number(group.iloc[0].get("automation_target_value")))
    if range_only:
        result.update(status="multiple_cycles_range_only", cycle_ambiguity=info["status"],
                      branch_qualification="Multiple recorded cycles: no individual cycle selected.",
                      direction_audit=info)
    else:
        hot_command = float(command.iloc[turn])
        result["heating_recorded"] = turn > 0 and hot_command > start_command + tolerance
        tail = command.iloc[turn:]
        changes = tail.diff().dropna()
        falling_steps = -changes[changes < -tolerance]
        falling = len(falling_steps)
        changing = int((changes.abs() > tolerance).sum())
        drop = hot_command - end_command
        cooling_recorded = bool(len(tail) >= 6 and falling >= 4 and changing > 0
                                and falling / changing >= 0.55
                                and drop >= max(1.0, info["command_span_mA"] * 0.05))
        logged_step = float(falling_steps.median()) if len(falling_steps) else None
        completion = cooling_completion(start_command, end_command, logged_step, cooling_recorded)
        cooling_complete = completion["cooling_complete"]
        result.update(completion)
        result.update(cooling_recorded=cooling_recorded,
                      cooling_falling_command_steps=falling, cooling_command_drop_mA=drop,
                      turnaround_current_set_mA=hot_command,
                      cooling_end_current_set_mA=end_command if cooling_recorded else None,
                      status="complete_cycle" if cooling_complete else "partial_cooling" if cooling_recorded else "heating_only_no_sustained_cooling")
    recorded = pd.to_numeric(group.get("strain_pct", pd.Series(index=group.index, dtype=float)), errors="coerce")
    length = None
    initial = _number(initial_length_mm)
    if initial is not None or length_mm is not None:
        position = pd.to_numeric(group.get("position_mm", pd.Series(index=group.index, dtype=float)), errors="coerce")
        if length_mm is not None:
            length = pd.to_numeric(length_mm.reindex(group.index), errors="coerce")
        else:
            length = initial + position if position.notna().any() else initial * (1.0 + recorded / 100.0)
        if not np.isfinite(length).all() or float(length.min()) <= 0:
            result.update(status="invalid_calibrated_length_trace", reference_basis="invalid_length_trace")
            return result
        reference = float(length.min())
        values = (length - reference) / reference * 100.0
        result.update(reference_basis="per_episode_minimum_calibrated_length", reference_length_mm=reference)
    else:
        values = recorded
        result["reference_basis"] = "recorded_logger_strain_pct_differences"
    if not np.isfinite(values).all():
        result["status"] = "invalid_recorded_strain_trace"
        return result
    start, end = map(float, (values.iloc[0], values.iloc[-1]))
    result["max_strain_pct"] = float(values.max() - values.min())
    if not range_only:
        hot = float(values.iloc[turn])
        if result["heating_recorded"]:
            result["heating_strain_pct"] = hot - start
        if cooling_recorded:
            result["partial_cooling_strain_pct"] = end - hot
            if result["heating_recorded"]:
                result["partial_residual_strain_pct"] = end - start
        if cooling_complete:
            result["cooling_strain_pct"] = result["partial_cooling_strain_pct"]
            result["residual_strain_pct"] = result["partial_residual_strain_pct"]
    endpoints = {}
    positions = [("start", 0), ("end", len(group) - 1)] if range_only else [("start", 0), ("turnaround", turn), ("end", len(group) - 1)]
    for name, position in positions:
        row = group.iloc[position]
        endpoints[name] = dict(raw_row_index=_number(row.get(RAW)), elapsed_s=_number(row.get("elapsed_s")),
                               current_set_mA=float(command.iloc[position]), current_measured_mA=_number(measured.iloc[position]),
                               recorded_strain_pct=_number(recorded.iloc[position]), derived_strain_pct=float(values.iloc[position]),
                               length_mm=_number(length.iloc[position]) if length is not None else None,
                               actual_stress_mpa=_number(row.get("stress_mpa")),
                               automation_phase=str(row.get("automation_phase")))
    result["endpoints"] = endpoints
    return result
