import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from forex_ai_analyst.research.edge_lab import registry
from forex_ai_analyst.research.edge_lab.hypotheses import crowding_exhaustion
from forex_ai_analyst.research.edge_lab.models import Verdict


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"EDGE_LAB_ROOT": self.tmp.name})
        self.env.start()
        self.manifest = copy.deepcopy(crowding_exhaustion.MANIFEST)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_register_is_idempotent_for_identical_content(self):
        first = registry.register(self.manifest)
        self.assertEqual(registry.register(copy.deepcopy(self.manifest)), first)
        self.assertEqual([e["event"] for e in registry.ledger()], ["REGISTERED"])

    def test_changed_content_under_same_id_is_refused(self):
        registry.register(self.manifest)
        with self.assertRaises(registry.RegistryError):
            registry.register({**self.manifest, "markets": ["BTC-USDT"]})

    def test_tampered_manifest_file_is_detected(self):
        registry.register(self.manifest)
        path = registry.manifest_path(self.manifest["hypothesis_id"])
        path.write_text(path.read_text().replace("BTC-USDT", "BTC-USDC"))
        with self.assertRaises(registry.RegistryError):
            registry.load(self.manifest["hypothesis_id"])

    def test_holdout_opens_exactly_once(self):
        registry.register(self.manifest)
        self.assertEqual(registry.open_holdout(self.manifest["hypothesis_id"]),
                         {"start": "2025-07-01", "end": "2026-09-01"})
        with self.assertRaises(registry.RegistryError):
            registry.open_holdout(self.manifest["hypothesis_id"])

    def test_trials_and_verdicts_are_appended_never_rewritten(self):
        hid = self.manifest["hypothesis_id"]
        registry.register(self.manifest)
        registry.record_trial(hid, {"exit": "fixed_2r"}, {"sharpe": -0.4})
        registry.record_trial(hid, {"exit": "oi_unwind_or_max_hold"}, {"sharpe": 0.2})
        registry.record_verdict(hid, Verdict.REJECTED, ["oos sharpe below gate"])
        self.assertEqual(registry.trial_count(hid), 2)
        self.assertEqual([e["event"] for e in registry.ledger(hid)], ["REGISTERED", "TRIAL", "TRIAL", "VERDICT"])

    def test_unregistered_hypothesis_cannot_record_trials(self):
        with self.assertRaises(registry.RegistryError):
            registry.record_trial("nope_v1", {}, {})

    def test_overlapping_periods_and_oversized_grids_are_rejected(self):
        overlapping = {**self.manifest, "validation_period": ["2023-06-01", "2025-07-01"]}
        self.assertIn("overlap", registry.validation_error(overlapping))
        oversized = {**self.manifest, "parameter_grid": {**self.manifest["parameter_grid"], "extra": [1, 2]}}
        self.assertIn("exceeds", registry.validation_error(oversized))


    def test_supersede_keeps_the_old_version_on_record(self):
        registry.register(crowding_exhaustion.MANIFEST_V1)
        registry.register(crowding_exhaustion.MANIFEST_V2)
        registry.supersede("crowding_exhaustion_v1", "crowding_exhaustion_v2", "OI coverage")
        event = registry.ledger("crowding_exhaustion_v1")[-1]
        self.assertEqual((event["event"], event["successor"], event["trials_before_supersede"]),
                         ("SUPERSEDED", "crowding_exhaustion_v2", 0))
        self.assertEqual(registry.load("crowding_exhaustion_v1")["discovery_period"][0], "2021-01-01")


class CrowdingExhaustionManifestTests(unittest.TestCase):
    V1_HASH = "e4c01d45d15adb8ce22c8e101ac101cd739e08786a2c21a4c327c1721a770263"

    def test_registered_v1_content_is_frozen(self):
        from forex_ai_analyst.research.edge_lab.models import content_hash
        self.assertEqual(content_hash(crowding_exhaustion.MANIFEST_V1), self.V1_HASH)

    def test_v2_differs_from_v1_only_in_identity_and_discovery_start(self):
        v1, v2 = crowding_exhaustion.MANIFEST_V1, crowding_exhaustion.MANIFEST_V2
        changed = {k for k in set(v1) | set(v2) if v1.get(k) != v2.get(k)}
        self.assertEqual(changed, {"hypothesis_id", "supersedes", "discovery_period", "data_coverage"})
        self.assertEqual(v2["discovery_period"], ["2021-12-01", "2024-01-01"])


    def test_manifest_is_valid_and_grid_is_capped_at_32(self):
        self.assertIsNone(registry.validation_error(crowding_exhaustion.MANIFEST))
        configs = crowding_exhaustion.parameter_configs()
        self.assertEqual(len(configs), 32)
        self.assertEqual(len({tuple(sorted(c.items())) for c in configs}), 32)

    def test_directions_are_separate_and_gates_match_the_plan(self):
        m = crowding_exhaustion.MANIFEST
        self.assertEqual(m["directions"], ["SHORT", "LONG"])
        self.assertEqual(m["promotion_gate"]["min_oos_sharpe"], 1.0)
        self.assertEqual(m["promotion_gate"]["min_deflated_sharpe_probability"], 0.90)
        self.assertEqual(m["event_study_gate"]["min_discovery_events"], 100)
        self.assertNotIn("READY_FOR_LIVE_MONEY", m["verdicts"])

    def test_unavailable_history_is_declared_not_proxied(self):
        m = crowding_exhaustion.MANIFEST
        self.assertEqual(m["data"]["liquidations"], "UNAVAILABLE")
        self.assertEqual(m["data"]["order_book_depth"], "LIVE_ONLY")


if __name__ == "__main__":
    unittest.main()
