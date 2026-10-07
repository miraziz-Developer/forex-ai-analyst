import unittest

from forex_ai_analyst.forex import scalp_multi_study as s

START = 1_700_000_000_000 // 3_600_000 * 3_600_000


def minute(i, mid, half_range=0.00005, spread=0.00002):
    return {"datetime": START + i * s.M1,
            "bid_open": mid, "bid_high": mid + half_range, "bid_low": mid - half_range, "bid_close": mid,
            "ask_open": mid + spread, "ask_high": mid + half_range + spread, "ask_low": mid - half_range + spread,
            "ask_close": mid + spread}


def wavy(n, start=1.1):
    """Minutes swinging +-8 pips with a period of an hour: enough range for ATR and signals."""
    import math
    return [minute(i, start + 0.0008 * math.sin(2 * math.pi * i / 60)) for i in range(n)]


def signal_at(n, k, side):
    sig = s._empty(n)
    sig["le" if side > 0 else "se"][k] = True
    return sig


class IndicatorTest(unittest.TestCase):
    def test_slope_of_a_line(self):
        self.assertAlmostEqual(s.slope([2.0 * i for i in range(30)])[-1], 2.0)

    def test_crosses(self):
        self.assertTrue(s.up_cross([1, 3], 2.0, 1))
        self.assertFalse(s.up_cross([3, 4], 2.0, 1))
        self.assertTrue(s.down_cross([3, 1], [2, 2], 1))
        self.assertFalse(s.up_cross([None, 3], 2.0, 1))

    def test_rmi_and_stochastic_bounds(self):
        closes = [1 + 0.01 * ((i * 7) % 11) for i in range(80)]
        self.assertTrue(all(0 <= x <= 100 for x in s.rmi(closes) if x is not None))
        bars = [{"high": c + 0.01, "low": c - 0.01, "close": c} for c in closes]
        k, d = s.stochastic(bars)
        self.assertTrue(all(0 <= x <= 100 for x in k + d if x is not None))

    def test_supertrend_sits_below_a_rising_market_and_flips(self):
        up = [{"high": 1 + 0.01 * i + 0.005, "low": 1 + 0.01 * i - 0.005, "close": 1 + 0.01 * i} for i in range(40)]
        down = [{"high": 1.4 - 0.02 * i + 0.005, "low": 1.4 - 0.02 * i - 0.005, "close": 1.4 - 0.02 * i}
                for i in range(40)]
        line = s.supertrend(up + down)
        self.assertLess(line[39], up[39]["close"])
        self.assertGreater(line[-1], down[-1]["close"])

    def test_pivot_reversal_arms_after_confirmation(self):
        highs = [1, 2, 3, 4, 9, 4, 3, 3, 3]
        bars = [{"high": h, "low": h - 0.5, "close": h - 0.2} for h in highs]
        sig = s.r4_pivot_reversal(bars)
        self.assertIsNone(sig["lstop"][5])               # pivot at bar 4 needs two bars to the right
        self.assertAlmostEqual(sig["lstop"][6], 9 + 0.1 * s.PIP)

    def test_every_rule_runs_on_minute_data(self):
        m1 = wavy(3000)
        for rule in s.RULES:
            trades = s.rule_trades("EURUSD", m1, rule)
            for t in trades:
                self.assertGreater(t["risk_pips"], 0)
                self.assertEqual(t["rule"], rule.name)


class ExecutionTest(unittest.TestCase):
    def test_long_market_entry_buys_ask_and_stops_on_bid(self):
        m1 = [minute(i, 1.1) for i in range(300)] + [minute(300 + i, 1.1 - 0.0001 * i) for i in range(300)]
        bars = s.base.aggregate(m1, 5 * s.M1)
        k = 50                                           # signal at the close of bar 50 (minute 254)
        trades = s.execute("EURUSD", "t", m1, bars, signal_at(len(bars), k, 1), 2.0, None, 1000)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["side"], 1)
        self.assertLessEqual(trades[0]["r_gross"], -1.0)               # a minute can open beyond the stop
        self.assertGreater(trades[0]["r_gross"], -1.3)

    def test_target_and_spread_cost(self):
        m1 = [minute(i, 1.1) for i in range(300)] + [minute(300 + i, 1.1 + 0.0001 * i) for i in range(300)]
        bars = s.base.aggregate(m1, 5 * s.M1)
        trades = s.execute("EURUSD", "t", m1, bars, signal_at(len(bars), 50, 1), 1.0, 1.0, 1000)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["r_gross"], 1.0, places=6)

    def test_position_closed_before_a_weekend_gap(self):
        m1 = [minute(i, 1.1) for i in range(300)] + [minute(3000 + i, 1.2) for i in range(300)]
        bars = s.base.aggregate(m1, 5 * s.M1)
        trades = s.execute("EURUSD", "t", m1, bars, signal_at(len(bars), 50, 1), 1000.0, None, 1000)
        self.assertEqual(len(trades), 1)
        self.assertLess(trades[0]["r_gross"], 0)         # closed at Friday's bid, not at Monday's jump

    def test_stop_entry_reverses_position(self):
        m1 = [minute(i, 1.1) for i in range(300)] + [minute(300 + i, 1.1 - 0.0001 * i) for i in range(100)]
        bars = s.base.aggregate(m1, 5 * s.M1)
        sig = signal_at(len(bars), 50, 1)
        for k in range(55, len(bars)):
            sig["sstop"][k] = 1.0995
        trades = s.execute("EURUSD", "t", m1, bars, sig, 100.0, None, 1000)
        self.assertEqual([t["side"] for t in trades][:1], [1])
        self.assertLess(trades[0]["r_gross"], 0)

    def test_confluence_needs_two_rules_one_way(self):
        m1 = wavy(1200)
        bars = s.base.aggregate(m1, 5 * s.M1)
        t0 = bars[100]["start"] + 60_000
        one = [{"rule": "a", "side": 1, "entry_ms": t0}, {"rule": "a", "side": 1, "entry_ms": t0 + 60_000}]
        self.assertEqual(s.confluence("EURUSD", m1, one), [])
        two = one + [{"rule": "b", "side": 1, "entry_ms": t0}]
        trades = s.confluence("EURUSD", m1, two)
        self.assertEqual(trades[0]["side"], 1)
        mixed = two + [{"rule": "c", "side": -1, "entry_ms": t0}]
        self.assertEqual(s.confluence("EURUSD", m1, mixed), [])

    def test_gate_refuses_and_min_stop_widens(self):
        m1 = [minute(i, 1.1) for i in range(300)] + [minute(300 + i, 1.1 - 0.0001 * i) for i in range(300)]
        bars = s.base.aggregate(m1, 5 * s.M1)
        sig = signal_at(len(bars), 50, 1)
        self.assertEqual(s.execute("EURUSD", "t", m1, bars, sig, 2.0, None, 1000, gate=lambda side, i: False), [])
        wide = s.execute("EURUSD", "t", m1, bars, sig, 2.0, None, 1000, min_stop=6 * s.PIP)
        self.assertAlmostEqual(wide[0]["risk_pips"], 6.0)

    def test_variant_gate_hours_and_trend(self):
        rising = [minute(i, 1.1 + i * 0.000002) for i in range(40 * 60)]
        gate = s.make_gate(rising, s.Variant("x", hours=(0,), trend=True))
        late = len(rising) - 1
        hour = s.datetime.fromtimestamp(rising[late]["datetime"] / 1000, s.timezone.utc).hour
        self.assertEqual(gate(1, late), hour == 0)
        trend_only = s.make_gate(rising, s.Variant("y", trend=True))
        self.assertTrue(trend_only(1, late))
        self.assertFalse(trend_only(-1, late))
        self.assertIsNone(s.make_gate(rising, s.Variant("base")))

    def test_selected_keeps_positive_rules_of_one_variant(self):
        dev = {"W:r1_ribbon": {"cost_0.7": {"mean_r": 0.1}}, "W:r2_ribbon_stochastic": {"cost_0.7": {"mean_r": -0.1}},
               "S:r3_momentum": {"cost_0.7": {"mean_r": 0.2}}, "W:portfolio": {"cost_0.7": {"mean_r": 0.3}}}
        self.assertEqual(s.selected(dev, "W"), ["r1_ribbon"])

    def test_fade_swaps_sides_and_turns_stops_into_limits(self):
        sig = s._empty(3)
        sig["le"][1], sig["xs"][2], sig["lstop"][0] = True, True, 1.2
        f = s.fade(sig)
        self.assertTrue(f["se"][1])
        self.assertTrue(f["xl"][2])
        self.assertEqual(f["slimit"][0], 1.2)
        self.assertIsNone(f["lstop"][0])

    def test_sell_limit_fills_only_when_traded_through(self):
        m1 = [minute(i, 1.1) for i in range(300)] + [minute(300 + i, 1.1 + 0.00001 * i) for i in range(100)]
        bars = s.base.aggregate(m1, 5 * s.M1)
        sig = s._empty(len(bars))
        for k in range(55, len(bars)):
            sig["slimit"][k] = 1.1003
        trades = s.execute("EURUSD", "t", m1, bars, sig, 100.0, None, 1000)
        self.assertEqual(trades, [])                      # still open at the end: filled, never closed
        m1 = m1 + [minute(400 + i, 1.0990) for i in range(200)]
        bars = s.base.aggregate(m1, 5 * s.M1)
        sig = s._empty(len(bars))
        for k in range(55, len(bars)):
            sig["slimit"][k] = 1.1003
        sig["xs"][100] = True
        trades = s.execute("EURUSD", "t", m1, bars, sig, 100.0, None, 1000)
        self.assertEqual(trades[0]["side"], -1)
        self.assertGreater(trades[0]["r_gross"], 0)
        # filled at the limit, never better: price had to reach 1.1003 + 0.2 pip (bid high = mid + 0.5 pip)
        first_fill = min(i for i, b in enumerate(m1) if b["bid_high"] >= 1.1003 + s.THROUGH)
        self.assertEqual(trades[0]["entry_ms"], m1[first_fill]["datetime"])

    def test_bigger_bars_give_wider_stops(self):
        m1 = wavy(6000)
        small = s.rule_trades("EURUSD", m1, s.RULES[6])
        big = s.rule_trades("EURUSD", m1, s.RULES[6], minutes=60)
        if small and big:
            self.assertGreater(min(t["risk_pips"] for t in big), max(t["risk_pips"] for t in small) / 4)
        self.assertLess(len(big), len(small) + 1)

    def test_evaluate_subtracts_cost_in_r(self):
        trades = [{"pair": "EURUSD", "day": "2025-01-0%d" % (i % 5 + 1), "r_gross": 1.0 if i % 2 else -1.0,
                   "risk_pips": 7.0} for i in range(20)]
        self.assertAlmostEqual(s.evaluate(trades, 0.0)["mean_r"], 0.0)
        self.assertAlmostEqual(s.evaluate(trades, 0.7)["mean_r"], -0.1)


if __name__ == "__main__":
    unittest.main()
