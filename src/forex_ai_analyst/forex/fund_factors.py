"""Dollar carry and market-based macro momentum, monthly (docs/FUND_FACTORS_STUDY.md).

    python3 -m forex_ai_analyst.forex.fund_factors
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev

from forex_ai_analyst.forex import fx_factors as ff
from forex_ai_analyst.forex.data import Market, load_yahoo

EQUITY = {"USD": "^GSPC", "EUR": "^GDAXI", "GBP": "^FTSE", "JPY": "^N225", "AUD": "^AXJO", "NZD": "^NZ50",
          "CAD": "^GSPTSE", "CHF": "^SSMI"}
ALL = ("USD",) + ff.CURRENCIES
GATE = {**ff.GATE, "min_bootstrap_p": 0.95}


def month_end_equity() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for ccy, symbol in EQUITY.items():
        for bar in load_yahoo(Market(ccy, symbol, 0.0, pct=True), "1d"):
            month = datetime.fromtimestamp(bar["datetime"] / 1000, timezone.utc).strftime("%Y-%m")
            out.setdefault(ccy, {})[month] = bar["close"]
    return out


def dollar_carry(i: int, months: list[str], rates: dict) -> dict[str, float] | None:
    rate_month = months[i - 1] if i else months[i]
    r_usd = ff._rate(rates, "USD", rate_month)
    foreign = [ff._rate(rates, c, rate_month) for c in ff.CURRENCIES]
    if r_usd is None or None in foreign:
        return None
    sign = 1.0 if sum(foreign) / len(foreign) > r_usd else -1.0
    return {c: sign / len(ff.CURRENCIES) for c in ff.CURRENCIES}


def zscores(values: dict[str, float]) -> dict[str, float]:
    mu, sd = sum(values.values()) / len(values), pstdev(values.values())
    return {k: (v - mu) / sd if sd > 0 else 0.0 for k, v in values.items()}


def macro_momentum(i: int, months: list[str], rates: dict, equity: dict) -> dict[str, float] | None:
    if i < 13:
        return None
    t, rate_now, rate_then = months[i], months[i - 1], months[i - 13]      # rates known through t-1
    d_rate, eq = {}, {}
    for c in ALL:
        a, b = ff._rate(rates, c, rate_now), ff._rate(rates, c, rate_then)
        e_now, e_then = equity.get(c, {}).get(t), equity.get(c, {}).get(months[i - 12])
        if None in (a, b, e_now, e_then):
            return None
        d_rate[c], eq[c] = a - b, e_now / e_then - 1
    zr, ze = zscores(d_rate), zscores(eq)
    return ff._rank_weights({c: zr[c] + ze[c] for c in ALL})


def backtest(months: list[str], excess: dict, decide) -> dict[str, float]:
    returns, held = {}, {}
    for i in range(len(months) - 1):
        weights = decide(i)
        if weights is None:
            continue
        nxt = months[i + 1]
        turnover = sum(abs(weights.get(c, 0) - held.get(c, 0)) for c in set(weights) | set(held) if c != "USD")
        gross = sum(abs(w) for c, w in weights.items() if c != "USD")
        ret = sum(w * excess[nxt].get(c, 0.0) for c, w in weights.items() if c != "USD")
        returns[nxt] = ret - turnover * ff.TURNOVER_COST - gross * ff.SWAP_MARKUP / 12
        held = weights
    return returns


def verdict(series: dict[str, float]) -> dict:
    s, p = ff.stats(series), ff.block_bootstrap_p(series)
    reasons = []
    if s.get("sharpe", 0) < GATE["min_sharpe"]:
        reasons.append(f"Sharpe {s.get('sharpe')} < {GATE['min_sharpe']}")
    if p < GATE["min_bootstrap_p"]:
        reasons.append(f"P(mean > 0) {p:.2f} < {GATE['min_bootstrap_p']}")
    if s.get("first_half_mean", 0) <= 0 or s.get("second_half_mean", 0) <= 0:
        reasons.append("not positive in both halves")
    if s.get("max_drawdown", -1) < GATE["max_drawdown"]:
        reasons.append(f"max drawdown {s.get('max_drawdown')} < {GATE['max_drawdown']}")
    return {**s, "p_mean_positive": round(p, 3), "passes": not reasons, "reasons": reasons}


def main() -> None:
    spots, rates, equity = ff.month_end_spots(), ff.load_rates(), month_end_equity()
    months, excess = ff.build_panel(spots, rates)
    raw = {"DC": backtest(months, excess, lambda i: dollar_carry(i, months, rates)),
           "MM": backtest(months, excess, lambda i: macro_momentum(i, months, rates, equity))}
    scaled = {k: ff.vol_scaled(v) for k, v in raw.items()}
    common = sorted(set(scaled["DC"]) & set(scaled["MM"]))
    report = {k: verdict(v) for k, v in scaled.items()}
    report["combined_info"] = verdict(ff.vol_scaled({m: (scaled["DC"][m] + scaled["MM"][m]) / 2 for m in common}))
    for name, r in report.items():
        print(f"{name}: {json.dumps(r)}")
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/fund_factors_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "report": report}, indent=2) + "\n")


if __name__ == "__main__":
    main()
