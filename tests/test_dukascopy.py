import lzma
import struct
import unittest
from datetime import date
from unittest.mock import patch

from forex_ai_analyst.forex import dukascopy


def blob(rows):
    return lzma.compress(b"".join(struct.pack(">5If", *r) for r in rows), format=lzma.FORMAT_ALONE)


class DukascopyTests(unittest.TestCase):
    def test_decode_uses_open_close_low_high_order_and_points(self):
        out = dukascopy.decode(blob([(3600, 108800, 108793, 108786, 108802, 1.5)]), date(2024, 1, 31), 1e-5)
        ms, o, h, lo, c = out[0]
        self.assertEqual(ms, 1706662800000 + 3600 * 1000)
        self.assertEqual((o, h, lo, c), (1.088, 1.08802, 1.08786, 1.08793))

    def test_hours_join_bid_and_ask(self):
        files = {"BID": blob([(0, 100000, 100010, 99990, 100020, 1.0)]),
                 "ASK": blob([(0, 100002, 100012, 99992, 100022, 1.0)])}
        with patch.object(dukascopy, "_raw", side_effect=lambda pair, day, side, hourly=False: files[side]):
            bars = dukascopy.hours("EURUSD", 2024, 1)
        self.assertEqual(len(bars), 1)
        self.assertAlmostEqual(bars[0]["ask_open"] - bars[0]["bid_open"], 0.00002)
        self.assertAlmostEqual(bars[0]["bid_high"], 1.0002)

    def test_empty_file_means_no_trading(self):
        self.assertEqual(dukascopy.decode(b"", date(2024, 2, 3), 1e-5), [])


if __name__ == "__main__":
    unittest.main()
