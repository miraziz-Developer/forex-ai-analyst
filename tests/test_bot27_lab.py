import unittest

from forex_ai_analyst.forex import bot27_lab as s

MON = 1_761_523_200_000            # 2025-10-27 00:00 UTC, a Monday


def minute(t, lo, hi, o=None, c=None, spread=0.00002):
    o = lo if o is None else o
    c = hi if c is None else c
    return {"datetime": t, "bid_open": o, "bid_high": hi, "bid_low": lo, "bid_close": c,
            "ask_open": o + spread, "ask_high": hi + spread, "ask_low": lo + spread, "ask_close": c + spread}


def long_pos():
    return {"side": 1, "entry": 1.1000, "dist": 0.0010, "sl": 1.0990, "tp": 1.1022, "partial": False}


class Bot27LabTests(unittest.TestCase):
    def test_stop_first_inside_a_minute(self):
        self.assertAlmostEqual(s.manage(long_pos(), minute(MON + 36_000_000, 1.0980, 1.1030, o=1.1000)), -1.0)

    def test_breakeven_then_partial_then_back_to_breakeven(self):
        pos = long_pos()
        t = MON + 36_000_000
        self.assertIsNone(s.manage(pos, minute(t, 1.0998, 1.1011)))           # +1.1R: BE and 60% out
        self.assertTrue(pos["partial"])
        self.assertAlmostEqual(pos["sl"], 1.1000 + 0.08 * 0.0010)
        r = s.manage(pos, minute(t + 60_000, 1.0995, 1.1005, o=1.1004))
        self.assertAlmostEqual(r, 0.6 * 1.0 + 0.4 * 0.08)

    def test_target_gives_the_blend(self):
        pos = long_pos()
        t = MON + 36_000_000
        s.manage(pos, minute(t, 1.0999, 1.1011))
        self.assertAlmostEqual(s.manage(pos, minute(t + 60_000, 1.1010, 1.1030)), 0.6 + 0.4 * 2.2)

    def test_short_uses_the_ask(self):
        pos = {"side": -1, "entry": 1.1000, "dist": 0.0010, "sl": 1.1010, "tp": 1.0978, "partial": False}
        # the bid never reaches the stop; the ask (bid + 2 pips) does
        self.assertAlmostEqual(s.manage(pos, minute(MON + 36_000_000, 1.0995, 1.1009, o=1.1000, spread=0.0002)), -1.0)

    def test_entry_hours(self):
        self.assertTrue(s.entries_allowed(MON + 7 * 3_600_000))
        self.assertFalse(s.entries_allowed(MON + 21 * 3_600_000))
        self.assertFalse(s.entries_allowed(MON + 4 * 86_400_000 + 18 * 3_600_000))   # Friday 18:00


if __name__ == "__main__":
    unittest.main()
