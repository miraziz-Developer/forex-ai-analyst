import unittest
from datetime import date, datetime, timezone

from forex_ai_analyst.forex import fomc_study as f


class FomcStudyTests(unittest.TestCase):
    def test_tone_reads_direction_next_to_the_right_nouns(self):
        self.assertEqual(f.tone("Inflation has risen and remains elevated."), 1.0)
        self.assertEqual(f.tone("The unemployment rate has declined."), 1.0)          # less slack: hawkish
        self.assertEqual(f.tone("Economic activity has slowed and inflation eased."), -1.0)
        self.assertEqual(f.tone("The unemployment rate has risen."), -1.0)
        self.assertIsNone(f.tone("The Committee decided to maintain the target range."))
        mixed = f.tone("Inflation remains elevated. Job gains have slowed.")
        self.assertEqual(mixed, 0.0)

    def test_new_york_open_handles_daylight_saving(self):
        summer = datetime.fromtimestamp(f.ny_open(date(2024, 7, 31), 16) / 1000, timezone.utc)
        winter = datetime.fromtimestamp(f.ny_open(date(2024, 1, 31), 16) / 1000, timezone.utc)
        self.assertEqual((summer.hour, winter.hour), (20, 21))

    def test_exit_two_weekdays_later_skips_the_weekend(self):
        self.assertEqual(f.two_weekdays_later(date(2024, 1, 31)), date(2024, 2, 2))   # Wed -> Fri
        self.assertEqual(f.two_weekdays_later(date(2024, 2, 1)), date(2024, 2, 5))    # Thu -> Mon

    def test_basket_long_usd_sells_eurusd_and_buys_usdjpy(self):
        day = date(2024, 1, 31)
        t0, t1 = f.ny_open(day, 16), f.ny_open(f.two_weekdays_later(day), 16)
        bars = {}
        for pair in f.PAIRS:
            usd_up = 1.01 if f.h1.USD_SIGN[pair] > 0 else 0.99        # the dollar rises 1% against everything
            bars[pair] = {t0: {"bid_open": 1.0, "ask_open": 1.0}, t1: {"bid_open": usd_up, "ask_open": usd_up}}
        self.assertGreater(f.basket_trade(bars, day, 1), 0.009)
        self.assertLess(f.basket_trade(bars, day, -1), -0.009)

    def test_statement_text_and_filter(self):
        page = ('<div class="col-xs-12 col-sm-8 col-md-8"><p>The Committee decided to raise the target range '
                'for the federal funds rate.</p></div>')
        text = f.statement_text(page)
        self.assertTrue(f.is_policy_statement(text))
        self.assertFalse(f.is_policy_statement("Discount rate action"))


if __name__ == "__main__":
    unittest.main()
