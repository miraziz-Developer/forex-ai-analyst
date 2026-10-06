"""Export MetaTrader 5 history to CSV for research (run on the Windows VM).

    python -m forex_ai_analyst.forex.mt5_export --years 10 --timeframes M15 H1 D1

Writes mt5_data/<SYMBOL>_<TF>.csv with columns datetime (UTC ISO), open, high,
low, close, tick_volume, spread (points). The broker's own spread column makes
cost modelling realistic. No account data or credentials are exported.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from forex_ai_analyst.forex.mt5_bot import resolve_symbol
from forex_ai_analyst.forex.regime_system_study import FX_ONLY


def export(mt5, years: int, timeframes: list[str], out_dir: Path) -> list[Path]:
    out_dir.mkdir(exist_ok=True)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * years)
    written = []
    for market in FX_ONLY:
        symbol = resolve_symbol(mt5, market.name)
        if not symbol:
            print(f"skip {market.name}: symbol not found at this broker")
            continue
        for tf in timeframes:
            rates = mt5.copy_rates_range(symbol, getattr(mt5, f"TIMEFRAME_{tf}"), start, end)
            if rates is None or len(rates) == 0:
                print(f"skip {symbol} {tf}: {mt5.last_error()}")
                continue
            path = out_dir / f"{market.name}_{tf}.csv"
            with path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["datetime", "open", "high", "low", "close", "tick_volume", "spread"])
                for r in rates:
                    writer.writerow([datetime.fromtimestamp(int(r["time"]), timezone.utc).isoformat(), r["open"], r["high"],
                                     r["low"], r["close"], r["tick_volume"], r["spread"]])
            print(f"{path}: {len(rates)} bars from {datetime.fromtimestamp(int(rates[0]['time']), timezone.utc).date()}")
            written.append(path)
    return written


def main() -> None:
    import MetaTrader5 as mt5

    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--timeframes", nargs="+", default=["M15", "H1", "D1"])
    parser.add_argument("--out", default="mt5_data")
    args = parser.parse_args()
    if not mt5.initialize():
        raise SystemExit(f"MetaTrader 5 initialize failed: {mt5.last_error()}")
    export(mt5, args.years, args.timeframes, Path(args.out))


if __name__ == "__main__":
    main()
