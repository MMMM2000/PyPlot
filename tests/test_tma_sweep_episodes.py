"""Deterministic offline acceptance checks; independent, explicit endpoints."""
import unittest

import numpy as np
import pandas as pd

from plotting.plugins.mini_dma.sweep_episodes import current_episodes, select_current_episode, metrics_for_episode, cooling_completion


def frame(current=None, strain=None, phase=None):
    current = current or [1, 10, 20, 30, 40, 40, 30, 20, 10, 5, 1]
    strain = strain or [0, 0, -2, -5, -8, -9, -7, -5, -2, -1, 0]
    n = len(current)
    return pd.DataFrame(dict(_raw_row_index=np.arange(n), elapsed_s=np.arange(n, dtype=float),
                             current_set_mA=current, current_mA=current, strain_pct=strain,
                             automation_phase=phase or ["current"] * n,
                             automation_target_value=50.0, plateau_index=0,
                             resistance_ohm=np.arange(n) + 100.0))


def join(*parts):
    data = pd.concat(parts, ignore_index=True)
    data["_raw_row_index"] = np.arange(len(data))
    data["elapsed_s"] = np.arange(len(data), dtype=float)
    return data


class RecipeBoundaryMetricsTests(unittest.TestCase):
    def test_closed_cycle_has_independent_expected_endpoints(self):
        m = metrics_for_episode(frame())
        self.assertEqual((m["heating_strain_pct"], m["cooling_strain_pct"], m["residual_strain_pct"]), (-9, 9, 0))
        self.assertEqual(m["max_strain_pct"], 9)
        self.assertEqual(m["max_current_mA"], 40)
        self.assertTrue(m["cooling_complete"])

    def test_pre_ramp_stale_target_island_not_joined_to_real_cycle(self):
        data = join(frame([1], [-9]), frame([1, 1, 1], [-6, -3, 0], ["target_ramp"] * 3), frame())
        old = data[data.automation_phase == "current"].copy()
        group, audit = select_current_episode(data, old)
        self.assertTrue(audit["selected"])
        self.assertEqual(group.iloc[0]["_raw_row_index"], 4)
        self.assertEqual(len(audit["rejected_fragments"]), 1)
        self.assertEqual(metrics_for_episode(group)["heating_strain_pct"], -9)

    def test_next_load_ramp_excluded_and_legitimate_holds_preserved(self):
        hold = frame([1, 1, 1], [0.5, 1, 1.5], ["current_hold"] * 3)
        following = frame([1, 1, 1], [3, 6, 9], ["target_ramp"] * 3)
        data = join(frame(), hold, following)
        group, _ = select_current_episode(data, data[data.automation_phase != "target_ramp"])
        self.assertEqual(len(group), 14)
        m = metrics_for_episode(group)
        self.assertEqual(m["cooling_strain_pct"], 10.5)
        self.assertEqual(m["residual_strain_pct"], 1.5)

    def test_two_reused_target_episodes_report_ambiguity(self):
        data = join(frame(), frame([1], [99], ["setup"]), frame())
        group, audit = select_current_episode(data, data[data.automation_phase == "current"])
        self.assertIsNone(group)
        self.assertEqual(audit["status"], "ambiguous_multiple_usable_episodes")

    def test_repeated_cycles_and_fatigue_not_represented_by_global_max(self):
        data = join(frame(), frame())
        m = metrics_for_episode(data)
        self.assertEqual(m["status"], "multiple_cycles_range_only")
        self.assertEqual((m["max_strain_pct"], m["max_current_mA"]), (9, 40))
        self.assertTrue(all(m[k] is None for k in ("heating_strain_pct", "cooling_strain_pct", "residual_strain_pct")))
        self.assertNotIn("turnaround", m["endpoints"])
        group, audit = select_current_episode(data, data)
        self.assertIsNotNone(group)
        self.assertFalse(audit["usable_singlecycle"])
        self.assertEqual(audit["status"], "selected_unique_multicycle_episode_range_only")
        data = frame()
        data["fatigue_cycle_index"] = [0] * 5 + [1] * 6
        m = metrics_for_episode(data)
        self.assertEqual(m["status"], "multiple_cycles_range_only")
        self.assertEqual(m["cycle_ambiguity"], "ambiguous_multiple_fatigue_cycles")

    def test_command_turn_is_unaffected_by_measured_overshoot(self):
        data = frame([1, 10, 20, 30, 40, 40, 39, 30, 20, 10, 1], [0, -1, -3, -6, -8, -9, -7, -5, -3, -1, 0])
        data["current_mA"] = [1, 10, 20, 30, 40, 39.9, 40.2, 30, 20, 10, 1]
        m = metrics_for_episode(data)
        self.assertEqual(m["endpoints"]["turnaround"]["raw_row_index"], 5)
        self.assertEqual(m["heating_strain_pct"], -9)
        self.assertEqual(m["max_current_mA"], 40)
        self.assertEqual(m["max_recorded_measured_current_mA"], 40.2)

    def test_partial_return_has_private_deltas_and_blank_public_recovery(self):
        data = frame([1, 10, 20, 30, 40, 39, 38, 37, 36, 35], [0, -1, -3, -6, -8, -7.8, -7.5, -7.2, -7, -6.8])
        m = metrics_for_episode(data)
        self.assertTrue(m["cooling_recorded"])
        self.assertFalse(m["cooling_complete"])
        self.assertIsNone(m["cooling_strain_pct"])
        self.assertIsNone(m["residual_strain_pct"])
        self.assertAlmostEqual(m["partial_cooling_strain_pct"], 1.2)
        self.assertEqual(m["partial_residual_strain_pct"], -6.8)

    def test_abrupt_shutdown_is_not_a_cooling_branch(self):
        data = frame([1, 10, 20, 30, 40, 1, 1, 1, 1, 1], [0, -1, -3, -6, -8, -8, -8, -8, -8, -8])
        m = metrics_for_episode(data)
        self.assertFalse(m["cooling_recorded"])
        self.assertEqual(m["heating_strain_pct"], -8)
        self.assertEqual(m["max_current_mA"], 40)

    def test_unknown_phase_never_falls_back_to_all_rows(self):
        data = frame().assign(automation_phase="unknown")
        group, audit = select_current_episode(data, data)
        self.assertIsNone(group)
        self.assertEqual(audit["status"], "no_qualified_current_episode")

    def test_verified_length_uses_clean_cycle_minimum_reference(self):
        data = frame()
        data["position_mm"] = data.strain_pct
        m = metrics_for_episode(data, initial_length_mm=100)
        self.assertEqual(m["reference_length_mm"], 91)
        self.assertAlmostEqual(m["heating_strain_pct"], -900 / 91)
        self.assertAlmostEqual(m["cooling_strain_pct"], 900 / 91)
        self.assertEqual(m["residual_strain_pct"], 0)

    def test_clock_restart_keeps_distinct_acquisitions(self):
        first, second = frame(), frame()
        data = join(first, second)
        data.loc[len(first):, "elapsed_s"] = np.arange(len(second))
        self.assertEqual(len(current_episodes(data)), 2)
        group, audit = select_current_episode(data, data)
        self.assertIsNone(group)
        self.assertEqual(audit["status"], "ambiguous_multiple_usable_episodes")

    def test_old_deduplication_does_not_remove_new_hold_evidence(self):
        data = join(frame(), frame([1, 1], [0.5, 1], ["current_hold"] * 2))
        old = data.iloc[:-1].copy()
        group, audit = select_current_episode(data, old)
        self.assertTrue(audit["selected"])
        self.assertEqual(len(group), len(data))
        self.assertEqual(metrics_for_episode(group)["residual_strain_pct"], 1)

    def test_one_observed_decrement_near_baseline_is_complete_without_new_endpoint(self):
        data = frame([1, 2, 3, 3, 2.8, 2.6, 2.4, 2.2, 2, 1.8, 1.6, 1.4, 1.2],
                     [0, -4, -8, -9, -8, -7, -6, -5, -4, -3, -2, -1, 0])
        m = metrics_for_episode(data)
        self.assertTrue(m["cooling_complete"])
        self.assertAlmostEqual(m["cooling_median_recorded_decrement_mA"], 0.2)
        self.assertEqual(m["endpoints"]["end"]["raw_row_index"], 12)
        self.assertEqual((m["heating_strain_pct"], m["cooling_strain_pct"], m["residual_strain_pct"]), (-9, 9, 0))
        truncated = metrics_for_episode(data.iloc[:-1])
        self.assertTrue(truncated["cooling_recorded"])
        self.assertFalse(truncated["cooling_complete"])
        self.assertIsNone(truncated["cooling_strain_pct"])

    def test_below_start_is_complete_and_abrupt_shutdown_cannot_use_step_allowance(self):
        self.assertTrue(cooling_completion(1.0, 0.8, 0.2, True)["cooling_complete"])
        self.assertTrue(cooling_completion(1.0, 1.2000000000000002, 0.2, True)["cooling_complete"])
        self.assertFalse(cooling_completion(1.0, 1.4, 0.2, True)["cooling_complete"])
        self.assertFalse(cooling_completion(1.0, 1.0, 39.0, False)["cooling_complete"])
        self.assertFalse(cooling_completion(1.0, 1.0, None, True)["cooling_complete"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
