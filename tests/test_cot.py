import unittest

from forex_ai_analyst.forex import cot


def weekly(start_year=2020, n=80, value=lambda k: k / 100):
    from datetime import date, timedelta
    d = date(start_year, 1, 7)          # a Tuesday
    return [((d + timedelta(weeks=k)).isoformat(), value(k)) for k in range(n)]


class CotTests(unittest.TestCase):
    def test_monday_decision_uses_only_the_report_published_by_then(self):
        rows = weekly()
        positions = {"EUR": rows}
        tuesday, value = rows[60]
        from datetime import date, timedelta
        monday_after = (date.fromisoformat(tuesday) + timedelta(days=6)).isoformat()
        friday_before_release = (date.fromisoformat(tuesday) + timedelta(days=3)).isoformat()
        self.assertAlmostEqual(cot.features_at(positions, "EURUSD", monday_after)["cot_net"], value)
        self.assertAlmostEqual(cot.features_at(positions, "EURUSD", friday_before_release)["cot_net"], rows[59][1])

    def test_inverted_and_cross_pairs_combine_legs(self):
        positions = {"JPY": weekly(value=lambda k: -0.2), "EUR": weekly(value=lambda k: 0.1)}
        day = "2021-07-20"
        self.assertAlmostEqual(cot.features_at(positions, "USDJPY", day)["cot_net"], 0.2)
        self.assertAlmostEqual(cot.features_at(positions, "EURJPY", day)["cot_net"], 0.3)

    def test_needs_a_year_of_history(self):
        self.assertIsNone(cot.features_at({"EUR": weekly(n=30)}, "EURUSD", "2020-08-30"))


if __name__ == "__main__":
    unittest.main()
