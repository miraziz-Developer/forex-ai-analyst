import random
import unittest

from forex_ai_analyst.research.edge_lab import event_study, features
from forex_ai_analyst.research.edge_lab.hypotheses import crowding_exhaustion as ce
from forex_ai_analyst.research.edge_lab.models import RawRecord

BAR = features.BAR_MS


def synthetic_frame(n=3200, seed=3):
    rng = random.Random(seed)
    price, cols = 100.0, {k: [] for k in ("open", "high", "low", "close", "quote_volume", "taker_buy_quote",
                                          "spot_high", "spot_low", "premium", "oi", "funding_pct")}
    oi = 1000.0
    for i in range(n):
        o = price
        price *= 1 + rng.gauss(0, 0.004)
        h, l = max(o, price) * (1 + abs(rng.gauss(0, 0.001))), min(o, price) * (1 - abs(rng.gauss(0, 0.001)))
        q = 1e6 * (1 + rng.random())
        oi *= 1 + rng.gauss(0, 0.002)
        for key, value in (("open", o), ("high", h), ("low", l), ("close", price), ("quote_volume", q),
                           ("taker_buy_quote", q * rng.uniform(0.3, 0.7)), ("spot_high", h * 0.999),
                           ("spot_low", l * 0.999), ("premium", rng.gauss(0, 0.0005)),
                           ("oi", None if i % 500 == 7 else oi), ("funding_pct", rng.uniform(0, 100))):
            cols[key].append(value)
    return features.Frame(pair="BTC-USDT", open_time=[i * BAR for i in range(n)],
                          decision_time=[(i + 1) * BAR for i in range(n)], **cols)


class RollingTests(unittest.TestCase):
    def test_rolling_percentile_uses_only_the_window_up_to_now(self):
        self.assertEqual(features.rolling_percentile([1, 2, 3, 4], 4, min_obs=1), [100.0, 100.0, 100.0, 100.0])
        out = features.rolling_percentile([4, 3, 2, 1], 4, min_obs=1)
        self.assertEqual([round(v, 1) for v in out], [100.0, 50.0, 33.3, 25.0])
        self.assertEqual(features.rolling_percentile([1, 9, 1, 9], 2, min_obs=1)[3], 100.0)

    def test_missing_values_are_skipped_not_zeroed(self):
        out = features.rolling_percentile([None, 5, None, 1], 4, min_obs=1)
        self.assertIsNone(out[0])
        self.assertIsNone(out[2])
        self.assertEqual(out[3], 50.0)

    def test_prior_extreme_excludes_current_bar_and_gaps(self):
        out = features.prior_extreme([1, 5, 2, None, 3, 4, 6], 2, True)
        self.assertEqual(out[:3], [None, None, 5])
        self.assertEqual(out[3], 5)
        self.assertIsNone(out[4])    # window [2, None]
        self.assertIsNone(out[5])    # window [None, 3]
        self.assertEqual(out[6], 4)


class PointInTimeTests(unittest.TestCase):
    def test_future_inputs_never_change_past_features(self):
        base = features.compute(synthetic_frame())
        t = 3000
        mutated = synthetic_frame()
        rng = random.Random(99)
        for name in ("open", "high", "low", "close", "quote_volume", "taker_buy_quote", "spot_high", "spot_low",
                     "premium", "oi", "funding_pct"):
            column = getattr(mutated, name)
            for i in range(t + 1, len(column)):
                column[i] = None if name in ("oi", "premium", "spot_high", "spot_low") and rng.random() < 0.3 \
                    else rng.uniform(1, 500)
        features.compute(mutated)
        for name, values in base.features.items():
            self.assertEqual(values[:t + 1], mutated.features[name][:t + 1], name)

    def test_align_uses_only_available_and_fresh_records(self):
        def rec(t, available, **values):
            return RawRecord("binance", "perpetual", "BTC-USDT", t, available, available, "t", values)
        bar = {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "quote_volume": 1.0, "taker_buy_quote": 0.5}
        perp = [rec(i * BAR, (i + 1) * BAR, **bar) for i in range(6)]
        oi = [rec(0, BAR, oi=10.0), rec(BAR, 3 * BAR, oi=11.0)]      # second becomes known only at bar 2's close
        frame = features.align("BTC-USDT", perp, [], [], [], oi)
        self.assertEqual(frame.oi[:3], [10.0, 10.0, 11.0])
        self.assertIsNone(frame.oi[4])                               # older than 30 minutes -> stale, not reused
        self.assertEqual(frame.spot_high, [None] * 6)                 # missing spot stays None


def crafted_frame(n=260):
    frame = synthetic_frame(n)
    f = frame.features
    for name in ("funding_pct", "oi_chg_1h_pct", "oi_chg_4h_pct", "premium_pct", "buy_ratio4_pct",
                 "sell_ratio4_pct", "up_impact_pct", "down_impact_pct"):
        f[name] = [50] * n
    for name in ("prior_high_20", "spot_prior_high_20"):
        f[name] = [1e9] * n
    for name in ("prior_low_20", "spot_prior_low_20"):
        f[name] = [0.0] * n
    frame.spot_high, frame.spot_low = [1.0] * n, [1.0] * n
    return frame


class EventTests(unittest.TestCase):
    CONFIG = {"funding_percentile": 90, "oi_change_lookback": "1h", "price_lookback_bars": 20,
              "confirmation": "prior_bar_low_break"}

    def arm_short_setup(self, frame, j):
        f = frame.features
        f["funding_pct"][j], f["oi_chg_1h_pct"][j], f["buy_ratio4_pct"][j], f["up_impact_pct"][j] = 95, 95, 85, 40
        f["prior_high_20"][j] = frame.high[j] - 1          # new high on the perpetual
        frame.low[j + 1], frame.close[j + 2] = 2.0, 1.0     # close breaks the prior bar's low two bars later

    def test_setup_then_break_is_one_event_and_cooldown_blocks_repeats(self):
        frame = crafted_frame()
        self.arm_short_setup(frame, 50)
        self.arm_short_setup(frame, 80)          # inside the 24h cooldown
        events = ce.detect_events(frame, self.CONFIG, "SHORT", "h_v2")
        self.assertEqual([e.features["bar_index"] for e in events], [52])
        self.assertEqual(events[0].decision_time_ms, frame.decision_time[52])

    def test_no_event_without_failed_flow_response(self):
        frame = crafted_frame()
        self.arm_short_setup(frame, 50)
        frame.features["up_impact_pct"][50] = 90   # buying still moves price efficiently
        self.assertEqual(ce.detect_events(frame, self.CONFIG, "SHORT", "h_v2"), [])

    def test_event_configs_drop_only_the_exit(self):
        configs = ce.event_configs()
        self.assertEqual(len(configs), 16)
        self.assertTrue(all("exit" not in c for c in configs))


class EventStudyTests(unittest.TestCase):
    GATE = {"min_discovery_events": 100, "min_bootstrap_positive_probability": 0.9, "min_pairs": 3,
            "leave_one_pair_out_must_hold": True}

    def test_outcome_uses_next_open_and_signs_by_direction(self):
        frame = synthetic_frame(300)
        i = 100
        frame.open[i + 1] = 100.0
        frame.close[i + 16] = 98.0
        row = event_study.outcome(frame, i, "SHORT")
        self.assertAlmostEqual(row["ret_4h"], 0.02)
        self.assertGreaterEqual(row["mfe"], 0)
        self.assertLessEqual(row["mae"], 0)
        self.assertIsNone(event_study.outcome(frame, 250, "SHORT"))   # no full 24h ahead

    def rows(self, value, pairs=("A", "B", "C", "D"), count=120):
        return [{"pair": pairs[k % len(pairs)], "decision_time_ms": k * 86_400_000,
                 **{f"ret_{h}": value + (k % 7) * 1e-4 for h in event_study.HORIZONS}, "mfe": 0.02, "mae": -0.01}
                for k in range(count)]

    def test_gate_outcomes(self):
        self.assertEqual(event_study.summarize(self.rows(0.01, count=50), self.GATE, 0.0014)["gate"]["verdict"],
                         "INSUFFICIENT_EVIDENCE")
        self.assertEqual(event_study.summarize(self.rows(0.01), self.GATE, 0.0014)["gate"]["verdict"], "PASS")
        self.assertEqual(event_study.summarize(self.rows(-0.01), self.GATE, 0.0014)["gate"]["verdict"], "REJECTED")
        below_cost = event_study.summarize(self.rows(0.0005), self.GATE, 0.0014)
        self.assertTrue(any("round-trip cost" in r for r in below_cost["gate"]["reasons"]))


if __name__ == "__main__":
    unittest.main()
