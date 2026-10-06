import unittest

from forex_ai_analyst.forex import fund_factors as fund
from forex_ai_analyst.forex import fx_factors as ff

MONTHS = [f"{2010 + k // 12}-{k % 12 + 1:02d}" for k in range(30)]


def flat_rates(usd, foreign):
    rates = {"USD": {m: usd for m in MONTHS}}
    for c in ff.CURRENCIES:
        rates[c] = {m: foreign for m in MONTHS}
    return rates


class FundFactorTests(unittest.TestCase):
    def test_dollar_carry_longs_the_basket_when_foreign_rates_are_higher(self):
        w = fund.dollar_carry(5, MONTHS, flat_rates(1.0, 3.0))
        self.assertTrue(all(abs(v - 1 / 7) < 1e-12 for v in w.values()))
        w = fund.dollar_carry(5, MONTHS, flat_rates(5.0, 3.0))
        self.assertTrue(all(abs(v + 1 / 7) < 1e-12 for v in w.values()))

    def test_dollar_carry_uses_only_last_months_rates(self):
        rates = flat_rates(5.0, 3.0)
        rates["USD"][MONTHS[5]] = 0.0          # this month's print is not known at its own month-end
        self.assertLess(fund.dollar_carry(5, MONTHS, rates)["EUR"], 0)

    def test_macro_momentum_prefers_rising_rates_and_strong_stocks(self):
        rates = flat_rates(2.0, 2.0)
        for m in MONTHS[10:]:
            rates["AUD"][m] = 4.0               # AUD hiking
            rates["JPY"][m] = 0.0               # JPY cutting
        equity = {c: {m: 100.0 for m in MONTHS} for c in fund.ALL}
        equity["AUD"][MONTHS[20]] = 130.0
        equity["JPY"][MONTHS[20]] = 70.0
        w = fund.macro_momentum(20, MONTHS, rates, equity)
        self.assertGreater(w["AUD"], 0)
        self.assertLess(w["JPY"], 0)
        self.assertAlmostEqual(sum(w.values()), 0.0)


if __name__ == "__main__":
    unittest.main()
