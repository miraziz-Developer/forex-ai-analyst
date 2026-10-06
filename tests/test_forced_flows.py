import unittest
from datetime import date

from forex_ai_analyst.forex import forced_flows as ff


class GotobiTests(unittest.TestCase):
    def test_gotobi_days_roll_back_from_weekends(self):
        days = ff.gotobi_dates(date(2026, 9, 1), date(2026, 9, 30))
        # Sep 2026: 5th is a Saturday -> Friday 4th; 20th is a Sunday -> Friday 18th; last day 30th (Wednesday)
        self.assertIn(date(2026, 9, 4), days)
        self.assertNotIn(date(2026, 9, 5), days)
        self.assertIn(date(2026, 9, 18), days)
        self.assertIn(date(2026, 9, 30), days)
        self.assertEqual(len([d for d in days if d.month == 9]), 6)


if __name__ == "__main__":
    unittest.main()
