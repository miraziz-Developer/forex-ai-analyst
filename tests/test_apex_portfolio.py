import unittest

from forex_ai_analyst.forex import apex_portfolio as ap


class ApexTests(unittest.TestCase):
    def test_inverse_vol_shifts_risk_to_the_calmer_engine_and_keeps_the_budget(self):
        calm = [0.5, -0.5] * 15
        wild = [5.0, -5.0] * 15
        monthly = {"crypto": calm, "index": wild}
        ap.BASE_RISK.update({"crypto": 1.0, "index": 1.0})
        a, b = ap.allocate(monthly, "A"), ap.allocate(monthly, "B")
        self.assertEqual(a[0], b[0])                                   # warm-up year: base weights
        self.assertLess(abs(b[20]), abs(a[20]))                        # the wild engine is cut back

    def test_vol_targeting_scales_within_bounds(self):
        monthly = {"crypto": [10.0, -10.0] * 15}
        ap.BASE_RISK["crypto"] = 1.0
        b, c = ap.allocate(monthly, "B"), ap.allocate(monthly, "C")
        self.assertAlmostEqual(c[20] / b[20], 0.5)                     # far above the 12% target: floor 0.5x


if __name__ == "__main__":
    unittest.main()
