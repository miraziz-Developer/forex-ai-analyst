import unittest
from datetime import datetime, timezone

from forex_ai_analyst.forex import asian_fade_study as af

HOUR = 3_600_000
START = int(datetime(2024, 1, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)      # Monday 00:00 UTC


def bars_from(mids, spread=0.00002):
    out = []
    for k, m in enumerate(mids):
        out.append({"datetime": START + k * HOUR, "bid_open": m, "bid_high": m, "bid_low": m, "bid_close": m,
                    "ask_open": m + spread, "ask_high": m + spread, "ask_low": m + spread, "ask_close": m + spread})
    return out


def wiggle(n):
    return [1.1 + (0.0001 if k % 2 else -0.0001) for k in range(n)]


class AsianFadeTests(unittest.TestCase):
    def test_spike_in_asian_hours_is_faded_and_closed_at_the_mean(self):
        mids = wiggle(24 + 1) + [1.1030, 1.1001, 1.0999] + wiggle(10)       # Tue: bar opening 01:00 spikes
        trades = af.trades_for("EURUSD", bars_from(mids), 2.0)
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t["side"], -1)
        # sold at the next open (bid 1.1001), bought back at the following open (ask 1.0999 + spread)
        self.assertAlmostEqual(t["net"], -((1.0999 + 0.00002) / 1.1001 - 1) - af.h1.MARKUP)

    def test_spike_in_european_hours_is_ignored(self):
        mids = wiggle(24 + 10) + [1.1030, 1.1001] + wiggle(10)              # Tue 10:00
        self.assertEqual(af.trades_for("EURUSD", bars_from(mids), 2.0), [])

    def test_trade_still_open_at_six_is_closed_at_the_six_oclock_open(self):
        mids = wiggle(24 + 3) + [1.1030] + [1.1030] * 6 + wiggle(5)          # spike at 03:00 that never reverts
        t = af.trades_for("EURUSD", bars_from(mids), 2.0)[0]
        exit_hour = datetime.fromtimestamp((START + (24 + 3 + 1 + t["hours"]) * HOUR) / 1000, timezone.utc).hour
        self.assertEqual(exit_hour, 6)


if __name__ == "__main__":
    unittest.main()
