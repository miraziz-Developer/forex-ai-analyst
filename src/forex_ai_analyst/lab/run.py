"""Run the full research study and write research_output/lab_report.{json,md}.

    python -m forex_ai_analyst.lab.run --start 2021-01-01 --end 2026-09-01

A family is promotable to live only if its stitched out-of-sample result
passes every PROMOTION_GATE check below; nothing here is permission to trade.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev

from forex_ai_analyst.lab import data, strategies, validation
from forex_ai_analyst.lab.engine import Costs, Sizing, run

PROMOTION_GATE = {"min_oos_sharpe": 0.8, "min_dsr": 0.90, "max_drawdown_pct": -35.0,
                  "min_positive_window_share": 0.6, "min_stress_sharpe": 0.3}
COST_SCENARIOS = {"taker": Costs(0.05, 0.02), "maker": Costs(0.02, 0.01), "stress": Costs(0.10, 0.05)}

_DATA: dict = {}


def _init(payload: dict) -> None:
    _DATA.update(payload)


def _evaluate(args: tuple) -> tuple[str, str, str, dict, dict]:
    config, pair, cost_name, risk = args
    bars = _DATA["bars"][(pair, config["tf"])]
    result = run(bars, strategies.build_signals(config, bars), _DATA["funding"][pair],
                 COST_SCENARIOS[cost_name], Sizing(risk_per_trade=risk))
    trades = result.trades
    summary = {"trades": len(trades),
               "win_rate_pct": round(100 * sum(t["net"] > 0 for t in trades) / len(trades), 1) if trades else None,
               "avg_r": round(mean(t["r"] for t in trades), 3) if trades else None,
               "fees_share_of_gross_pct": _fee_share(trades)}
    return config["key"], pair, cost_name, validation.daily_returns(result.equity), summary


def _fee_share(trades: list[dict]) -> float | None:
    gross = sum(abs(t["net"] + t["fees"] + t["funding"]) for t in trades)
    return round(100 * sum(t["fees"] for t in trades) / gross, 1) if gross else None


def _correlation(a: dict[str, float], b: dict[str, float]) -> float | None:
    days = sorted(set(a) & set(b))
    if len(days) < 30:
        return None
    xs, ys = [a[d] for d in days], [b[d] for d in days]
    mx, my, sx, sy = mean(xs), mean(ys), pstdev(xs), pstdev(ys)
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(days) / (sx * sy)


def inverse_vol_portfolio(streams: dict[str, dict[str, float]], lookback: int = 90) -> dict[str, float]:
    """Weights use only returns before each day (no look-ahead)."""
    names, days = list(streams), sorted(set().union(*streams.values()))
    out, history = {}, {n: [] for n in names}
    for day in days:
        vols = {n: pstdev(history[n][-lookback:]) for n in names if len(history[n]) >= 20}
        inv = {n: 1 / v for n, v in vols.items() if v > 0}
        total = sum(inv.values())
        out[day] = sum(inv[n] / total * streams[n].get(day, 0.0) for n in inv) if total else 0.0
        for n in names:
            history[n].append(streams[n].get(day, 0.0))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-09-01")
    parser.add_argument("--pairs", default="BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,BNB-USDT")
    parser.add_argument("--timeframes", default="1,4", help="bar sizes in hours")
    parser.add_argument("--risk", type=float, default=0.01, help="equity fraction risked per trade")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--out", default="research_output")
    args = parser.parse_args()

    started = time.time()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    pairs, tfs = [p.strip().upper() for p in args.pairs.split(",")], [int(x) for x in args.timeframes.split(",")]

    print(f"Loading data {args.start}..{args.end} for {', '.join(pairs)}", flush=True)
    bars, funding, hold = {}, {}, {}
    for pair in pairs:
        hourly = data.load_klines(pair, start, end, "1h")
        funding[pair] = data.load_funding(pair, start, end)
        for tf in tfs:
            bars[(pair, tf)] = hourly if tf == 1 else data.resample(hourly, tf)
        hold[pair] = validation.daily_returns([(b["datetime"], b["close"]) for b in hourly])
        print(f"  {pair}: {len(hourly)} hourly bars, {len(funding[pair])} funding events", flush=True)

    configs = strategies.configs(tfs)
    print(f"Backtesting {len(configs)} configs x {len(pairs)} pairs with {args.workers} workers", flush=True)
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context()
    jobs = [(c, p, "taker", args.risk) for c in configs for p in pairs]
    per_pair: dict[str, dict[str, dict]] = {c["key"]: {} for c in configs}
    trade_summary: dict[str, dict[str, dict]] = {c["key"]: {} for c in configs}
    with ctx.Pool(args.workers, initializer=_init, initargs=({"bars": bars, "funding": funding},)) as pool:
        for key, pair, _, rets, summary in pool.imap_unordered(_evaluate, jobs, chunksize=4):
            per_pair[key][pair], trade_summary[key][pair] = rets, summary

        # Equal capital per pair; each pair risks `risk` of its own sleeve per trade.
        returns = {key: validation.combine([per_pair[key][p] for p in pairs]) for key in per_pair}
        first_day = min(min(r) for r in returns.values() if r)
        last_day = args.end
        windows = validation.walk_forward_windows(first_day, last_day)
        wf = validation.walk_forward(configs, returns, windows)
        trial_sharpes = [validation.sharpe(list(r.values())) for r in returns.values()]

        # Cost sensitivity (plan item 3): re-run only the configs that were selected.
        selected = sorted({s["config"] for f in wf.values() for s in f["selections"]})
        by_key = {c["key"]: c for c in configs}
        extra = [(by_key[k], p, cost, args.risk) for k in selected for p in pairs for cost in ("maker", "stress")]
        cost_returns: dict[tuple[str, str], list] = {}
        for key, pair, cost, rets, _ in pool.imap_unordered(_evaluate, extra, chunksize=2):
            cost_returns.setdefault((key, cost), []).append(rets)

    def stitched(family: str, cost: str) -> dict[str, float]:
        out = {}
        for sel, (_, _, test_start, test_end) in zip(wf[family]["selections"], windows):
            combined = validation.combine(cost_returns[(sel["config"], cost)])
            out.update(validation.window(combined, test_start, test_end))
        return out

    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "period": [args.start, args.end],
              "pairs": pairs, "timeframes_h": tfs, "risk_per_trade": args.risk, "trials": len(configs),
              "walk_forward_windows": windows, "promotion_gate": PROMOTION_GATE, "families": {}}

    # Parameter choice already happened out-of-sample inside each walk-forward window; the only
    # choice made while looking at OOS results is which family to use. So the promotion DSR
    # deflates against the families' OOS Sharpes. The all-trials figure (every config's
    # full-period Sharpe, dominated by cost-driven dispersion of 1h configs) is kept as a
    # stricter reference.
    family_oos_sharpes = [validation.sharpe(list(wf[f]["oos"].values())) for f in wf]
    for family, result in wf.items():
        oos = result["oos"]
        values = [oos[d] for d in sorted(oos)]
        windows_positive = [s["test"].get("total_return_pct", 0) > 0 for s in result["selections"]]
        maker, stress = validation.stats(stitched(family, "maker")), validation.stats(stitched(family, "stress"))
        row = {"oos": validation.stats(oos), "dsr": validation.deflated_sharpe(values, family_oos_sharpes),
               "dsr_all_trials": validation.deflated_sharpe(values, trial_sharpes),
               "bootstrap": validation.block_bootstrap(values),
               "positive_window_share": round(sum(windows_positive) / len(windows_positive), 2) if windows_positive else 0,
               "maker_costs": maker, "stress_costs": stress, "selections": result["selections"]}
        s = row["oos"]
        checks = {
            "oos_sharpe": s.get("sharpe", 0) >= PROMOTION_GATE["min_oos_sharpe"],
            "deflated_sharpe": (row["dsr"] or 0) >= PROMOTION_GATE["min_dsr"],
            "max_drawdown": s.get("max_drawdown_pct", -100) >= PROMOTION_GATE["max_drawdown_pct"],
            "consistency": row["positive_window_share"] >= PROMOTION_GATE["min_positive_window_share"],
            "survives_stress_costs": stress.get("sharpe", 0) >= PROMOTION_GATE["min_stress_sharpe"],
        }
        row["checks"], row["promotable"] = checks, all(checks.values())
        report["families"][family] = row

    # Portfolio (plan item 2): inverse-vol blend of every family's OOS stream (no OOS cherry-picking).
    portfolio = inverse_vol_portfolio({f: wf[f]["oos"] for f in wf})
    report["portfolio_all_families"] = {"oos": validation.stats(portfolio),
                                        "bootstrap": validation.block_bootstrap([portfolio[d] for d in sorted(portfolio)])}
    oos_start = windows[0][2] if windows else args.start
    bench = validation.window(validation.combine([hold[p] for p in pairs]), oos_start, last_day)
    report["benchmark_equal_weight_buy_and_hold"] = validation.stats(bench)
    corr = {f"{a}/{b}": _correlation(hold[a], hold[b]) for i, a in enumerate(pairs) for b in pairs[i + 1:]}
    valid = [v for v in corr.values() if v is not None]
    report["pair_correlation"] = {"average": round(mean(valid), 3) if valid else None,
                                  "pairs": {k: round(v, 3) for k, v in corr.items() if v is not None}}
    fam_corr = {f"{a}/{b}": _correlation(wf[a]["oos"], wf[b]["oos"]) for i, a in enumerate(wf) for b in list(wf)[i + 1:]}
    report["family_correlation"] = {k: round(v, 3) for k, v in fam_corr.items() if v is not None}
    report["top_trials_full_period"] = sorted(
        ({"config": k, **validation.stats(r), "trades": sum(x["trades"] for x in trade_summary[k].values()),
          "fees_share_of_gross_pct_by_pair": {p: trade_summary[k][p]["fees_share_of_gross_pct"] for p in pairs}}
         for k, r in returns.items()), key=lambda x: x.get("sharpe", 0), reverse=True)[:15]
    report["runtime_seconds"] = round(time.time() - started, 1)

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    (out / "lab_report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "lab_report.md").write_text(render_markdown(report))
    print(render_markdown(report))


def _fmt(value, suffix=""):
    return "-" if value is None else f"{value}{suffix}"


def render_markdown(report: dict) -> str:
    lines = ["# Research lab report", "",
             f"Period {report['period'][0]}..{report['period'][1]}, pairs {', '.join(report['pairs'])}, "
             f"timeframes {report['timeframes_h']}h, risk/trade {report['risk_per_trade']:.2%}, "
             f"trials searched {report['trials']}, walk-forward windows {len(report['walk_forward_windows'])}.", "",
             "## Out-of-sample results (walk-forward, taker costs + real funding)", "",
             "DSR deflates against the families compared on OOS data; DSR(all) against every config searched "
             "(stricter reference, not used by the gate).", "",
             "| Family | Sharpe | CAGR % | Max DD % | DSR | DSR(all) | Win windows | Maker Sharpe | Stress Sharpe | Boot p5 CAGR % | Promotable |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for family, row in report["families"].items():
        s, b = row["oos"], row["bootstrap"]
        dsr = round(row["dsr"], 3) if row["dsr"] is not None else None
        dsr_all = round(row["dsr_all_trials"], 3) if row["dsr_all_trials"] is not None else None
        lines.append(f"| {family} | {_fmt(s.get('sharpe'))} | {_fmt(s.get('cagr_pct'))} | {_fmt(s.get('max_drawdown_pct'))} | "
                     f"{_fmt(dsr)} | {_fmt(dsr_all)} | {row['positive_window_share']:.0%} | {_fmt(row['maker_costs'].get('sharpe'))} | "
                     f"{_fmt(row['stress_costs'].get('sharpe'))} | {_fmt(b.get('cagr_p5'))} | "
                     f"{'YES' if row['promotable'] else 'no'} |")
    p, bh = report["portfolio_all_families"]["oos"], report["benchmark_equal_weight_buy_and_hold"]
    lines += ["", f"**Portfolio (inverse-vol, all families):** Sharpe {_fmt(p.get('sharpe'))}, "
              f"CAGR {_fmt(p.get('cagr_pct'))}%, max DD {_fmt(p.get('max_drawdown_pct'))}%.",
              f"**Benchmark (equal-weight buy & hold, same OOS period):** Sharpe {_fmt(bh.get('sharpe'))}, "
              f"CAGR {_fmt(bh.get('cagr_pct'))}%, max DD {_fmt(bh.get('max_drawdown_pct'))}%.",
              f"**Average pair correlation:** {_fmt(report['pair_correlation']['average'])} "
              "(close to 1 means the pairs are effectively one bet).", "",
              "## Selections per test window", ""]
    for family, row in report["families"].items():
        lines.append(f"**{family}**")
        for sel in row["selections"]:
            t = sel["test"]
            lines.append(f"- {sel['test_window']}: `{sel['config']}` -> Sharpe {_fmt(t.get('sharpe'))}, "
                         f"return {_fmt(t.get('total_return_pct'))}%")
        lines.append("")
    lines += ["## Promotion gate", "", "```", json.dumps(report["promotion_gate"], indent=2), "```"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
