import unittest
from datetime import date, datetime, timezone

from forex_ai_analyst.forex import ideas_study as s


def mbar(t, o, c, spread=0.5):
    h, low = max(o, c) + 1, min(o, c) - 1
    return {"datetime": t, "bid_open": o, "bid_high": h, "bid_low": low, "bid_close": c,
            "ask_open": o + spread, "ask_high": h + spread, "ask_low": low + spread, "ask_close": c + spread}


class IdeasTests(unittest.TestCase):
    def test_summary_halves_and_sharpe(self):
        rows = [{"t": s.SPLIT_2020 - 1, "r": 0.01}] * 10 + [{"t": s.SPLIT_2020 + 1, "r": -0.005}] * 10
        out = s.summary(rows, "r", s.SPLIT_2020, 252)
        self.assertEqual(out["halves_mean"], [0.01, -0.005])
        self.assertIn("sharpe", out)

    def test_fomc_window_uses_open_before_and_close_just_before_the_announcement(self):
        day = date(2025, 1, 29)
        start, end = s.ny(s.prev_weekday(day), 14), s.ny(day, 14)
        bars = {start: mbar(start, 100, 100), end - s.HOUR: mbar(end - s.HOUR, 101, 102)}
        r = s.window_return(bars, start, end)
        self.assertAlmostEqual(r, 102 / 100.5 - 1 - s.INDEX_EXTRA - s.NIGHT_SWAP)

    def test_ratio_trade_sells_the_rich_leg_and_exits_at_the_mean(self):
        n = 100
        a = [100.0 + (1 if k % 2 else -1) for k in range(n)] + [120.0, 100.0]
        b = [100.0] * (n + 2)
        rows = s.ratio_trades(list(range(n + 2)), a, b, 60, 20, 0.0, 0.0, 1)
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]["r"], 0)                       # sold a at 120, it came back to 100

    def test_orb_long_on_an_up_first_candle_hits_target(self):
        day = date(2025, 3, 4)
        t0 = s.ny(day, 9, 30)
        bars = [mbar(t0 + k * 60_000, 100 + k, 101 + k) for k in range(5)]          # up: 100 -> 105
        bars += [mbar(t0 + (5 + k) * 60_000, 105 + 3 * k, 108 + 3 * k) for k in range(60)]
        r = s.orb_day(bars, day)
        self.assertGreater(r["r"], 5)

    def test_orb_stop_first(self):
        day = date(2025, 3, 4)
        t0 = s.ny(day, 9, 30)
        bars = [mbar(t0 + k * 60_000, 100 + k, 101 + k) for k in range(5)]
        bars.append(mbar(t0 + 5 * 60_000, 105, 90))
        self.assertLess(s.orb_day(bars, day)["r"], -0.9)

    def test_weekly_long_short_ranks_and_charges_fees(self):
        t0 = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        universe = {}
        for i in range(20):
            growth = 1 + 0.001 * i
            universe[f"C{i}"] = ({t: 100 * growth ** ((t - t0) // s.DAY) for t in
                                  range(t0, t0 + 40 * s.WEEK, s.DAY)}, [])
        rows = s.weekly_long_short(universe, lambda p, t: int(p[1:]))
        self.assertTrue(rows)
        self.assertTrue(all(r["ls"] > -2 * s.CRYPTO_FEE for r in rows))
        self.assertGreater(rows[0]["ls"], 0)                      # faster growers long, slower short


if __name__ == "__main__":
    unittest.main()
