"""CFTC Commitments of Traders (legacy, futures only): speculative positioning per currency and gold.

Reports describe positions as of Tuesday and are published on Friday, so a
Monday decision may use only the report dated six or more days earlier.
"""
from __future__ import annotations

import csv
import io
import zipfile
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone

import requests

from forex_ai_analyst.forex.data import CACHE_DIR

URL = "https://www.cftc.gov/files/dea/history/{name}"
CODES = {"099741": "EUR", "096742": "GBP", "097741": "JPY", "232741": "AUD", "112741": "NZD", "090741": "CAD",
         "092741": "CHF", "088691": "GOLD"}
# market -> list of (asset, sign): pair exposure to speculative positioning
EXPOSURE = {"EURUSD": [("EUR", 1)], "GBPUSD": [("GBP", 1)], "AUDUSD": [("AUD", 1)], "NZDUSD": [("NZD", 1)],
            "USDCAD": [("CAD", -1)], "USDCHF": [("CHF", -1)], "USDJPY": [("JPY", -1)],
            "EURJPY": [("EUR", 1), ("JPY", -1)], "GBPJPY": [("GBP", 1), ("JPY", -1)], "XAUUSD": [("GOLD", 1)]}
PUBLICATION_LAG_DAYS = 6


def _archive(name: str, max_age_days: int | None) -> bytes:
    folder = CACHE_DIR / "cot"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    fresh = path.exists() and (max_age_days is None or
                               datetime.now(timezone.utc).timestamp() - path.stat().st_mtime < max_age_days * 86400)
    if not fresh:
        response = requests.get(URL.format(name=name), headers={"User-Agent": "Mozilla/5.0"}, timeout=180)
        response.raise_for_status()
        path.write_bytes(response.content)
    return path.read_bytes()


def load_positions(first_year: int = 2003) -> dict[str, list[tuple[str, float]]]:
    """{asset: [(as_of 'YYYY-MM-DD', net speculative position / open interest)]} sorted by date."""
    this_year = datetime.now(timezone.utc).year
    names = ["deacot1986_2016.zip"] + [f"deacot{y}.zip" for y in range(2017, this_year + 1)]
    series: dict[str, dict[str, float]] = {}
    for name in names:
        current = name == f"deacot{this_year}.zip"
        blob = _archive(name, 1 if current else None)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            text = io.TextIOWrapper(archive.open(archive.namelist()[0]), encoding="latin-1")
            for row in csv.DictReader(text):
                asset = CODES.get(row["CFTC Contract Market Code"].strip())
                day = row["As of Date in Form YYYY-MM-DD"].strip()
                if not asset or day[:4] < str(first_year):
                    continue
                oi = float(row["Open Interest (All)"])
                if oi > 0:
                    net = float(row["Noncommercial Positions-Long (All)"]) - float(row["Noncommercial Positions-Short (All)"])
                    series.setdefault(asset, {})[day] = net / oi
    return {asset: sorted(values.items()) for asset, values in series.items()}


def features_at(positions: dict, market: str, day: str) -> dict | None:
    """COT features for `market` at decision `day`, using only reports published by then."""
    cutoff = (date.fromisoformat(day) - timedelta(days=PUBLICATION_LAG_DAYS)).isoformat()
    net = pct = chg = 0.0
    for asset, sign in EXPOSURE[market]:
        rows = positions.get(asset, [])
        j = bisect_right([d for d, _ in rows], cutoff) - 1
        if j < 52:
            return None
        window = [v for _, v in rows[j - 51:j + 1]]
        value = rows[j][1]
        rank = 100 * sum(1 for v in window if v <= value) / len(window)
        net += sign * value
        pct += rank if sign > 0 else 100 - rank
        chg += sign * (value - rows[j - 4][1])
    legs = len(EXPOSURE[market])
    return {"cot_net": net, "cot_pct52": pct / legs, "cot_chg4": chg}
