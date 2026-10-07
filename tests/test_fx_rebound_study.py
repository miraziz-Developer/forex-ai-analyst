import unittest

from forex_ai_analyst.forex import fx_rebound_study as s

H = 3_600_000


def bar(i, o, c, spread=0.00002):
    h, low = max(o, c) + 0.0001, min(o, c) - 0.0001
    return {"datetime": i * H, "bid_open": o, "bid_high": h, "bid_low": low, "bid_close": c,
            "ask_open": o + spread, "ask_high": h + spread, "ask_low": low + spread, "ask_close": c + spread}


def series(tail):
    """800 quiet hours swinging +-2 pips, then the given closes."""
    out, price = [], 1.1
    for i in range(800):
        nxt = 1.1 + (0.0002 if i % 2 else -0.0002)
        out.append(bar(i, price, nxt))
        price = nxt
    for j, c in enumerate(tail):
        out.append(bar(800 + j, price, c))
        price = c
    return out


class FxReboundTest(unittest.TestCase):
    def test_crash_and_green_hour_buys_and_reaches_target(self):
        crash = [1.1 - 0.0015 * k for k in range(1, 20)]            # 285 pips down in 19 hours: far past 3 sigma
        tail = crash + [crash[-1] + 0.0004] + [crash[-1] + 0.0004 + 0.0020 * k for k in range(1, 30)]
        trades = s.trades_for("EURUSD", series(tail), s.Variant("base"))
        self.assertEqual(trades[0]["side"], 1)
        self.assertEqual(trades[0]["how"], "target")
        self.assertGreater(trades[0]["r"], 0)

    def test_long_only_ignores_spikes_up(self):
        spike = [1.1 + 0.0015 * k for k in range(1, 20)]
        tail = spike + [spike[-1] - 0.0004] + [spike[-1] - 0.0004 - 0.0005 * k for k in range(1, 30)]
        self.assertEqual(s.trades_for("EURUSD", series(tail), s.Variant("LONG", sides="long")), [])
        self.assertEqual(s.trades_for("EURUSD", series(tail), s.Variant("base"))[0]["side"], -1)

    def test_time_exit_after_24_hours(self):
        crash = [1.1 - 0.0015 * k for k in range(1, 20)]
        flat = [crash[-1] + 0.0004] + [crash[-1] + 0.0004 + (0.0001 if k % 2 else 0) for k in range(60)]
        trades = s.trades_for("EURUSD", series(crash + flat), s.Variant("base"))
        self.assertEqual(trades[0]["how"], "time")
        self.assertEqual((trades[0]["exit_ms"] - trades[0]["entry_ms"]) // H, 24)


if __name__ == "__main__":
    unittest.main()
