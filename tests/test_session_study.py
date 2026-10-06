import unittest
from datetime import datetime, timezone

from forex_ai_analyst.forex import session_study


def hourly(start, prices):
    t0 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    return [{"datetime": t0 + k * 3_600_000, "open": p, "high": p, "low": p, "close": p} for k, p in enumerate(prices)]


class SessionStudyTests(unittest.TestCase):
    def test_us_hours_trade_is_short_usd(self):
        prices = [1.0] * 12 + [1.0] + [1.0] * 7 + [1.01] + [1.01] * 3           # Monday 2026-10-05, 00:00-23:00
        bars = hourly("2026-10-05T00:00:00", prices)
        eur = session_study.session_trades(bars, "EURUSD", 0.0001, both=False)
        jpy = session_study.session_trades(bars, "USDJPY", 0.0001, both=False)
        self.assertAlmostEqual(eur[0]["gross"], 0.01)            # EURUSD up = USD down: short USD wins
        self.assertAlmostEqual(jpy[0]["gross"], -0.01)
        self.assertAlmostEqual(eur[0]["net"], 0.01 - 0.0001)

    def test_evening_leg_runs_to_next_weekday_noon_and_pays_more(self):
        prices = [1.0] * 20 + [1.0] * 16 + [0.99] + [0.99] * 11                 # Mon 20:00 -> Tue 12:00 falls 1%
        bars = hourly("2026-10-05T00:00:00", prices)
        rest = [t for t in session_study.session_trades(bars, "EURUSD", 0.0001, both=True) if t["leg"] == "rest"]
        self.assertEqual(len(rest), 1)
        self.assertAlmostEqual(rest[0]["gross"], 0.01)            # EURUSD down = long USD wins
        self.assertAlmostEqual(rest[0]["net"], 0.01 - 0.00015)

    def test_no_evening_leg_over_the_weekend(self):
        bars = hourly("2026-10-09T00:00:00", [1.0] * 24 * 4)                    # Friday to Monday
        legs = [t["leg"] for t in session_study.session_trades(bars, "EURUSD", 0.0, both=True)]
        self.assertNotIn("rest", legs[:1])
        self.assertEqual(legs.count("rest"), 0)


if __name__ == "__main__":
    unittest.main()
