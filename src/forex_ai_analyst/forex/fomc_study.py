"""FOMC statement tone and reaction drift on the USD basket (docs/FOMC_STUDY.md).

    python3 -m forex_ai_analyst.forex.fomc_study
"""
from __future__ import annotations

import html
import json
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from forex_ai_analyst.forex import h1_ml_study as h1
from forex_ai_analyst.forex.data import CACHE_DIR

BASE = "https://www.federalreserve.gov"
NEW_YORK = ZoneInfo("America/New_York")
PAIRS = tuple(h1.USD_SIGN)
FIRST_YEAR, LAST_DAY = 2010, "2026-09-30"
SPLIT = "2018-01-01"
GATE = {"min_events": 100, "min_p": 0.95, "min_pf": 1.2}

HAWK_NOUNS = r"inflation|prices?|wages?|growth|activity|economy|spending|demand|employment|job gains"
SLACK_NOUNS = r"unemployment|slack"
UP = (r"rising|rose|risen|increas\w*|higher|high|elevated|strong\w*|solid|robust|accelerat\w*|expand\w*|firm\w*|"
      r"picked up|upward")
DOWN = (r"falling|fell|declin\w*|lower|low|weak\w*|soft\w*|slow\w*|moderat\w*|eased|easing|subdued|contract\w*|"
        r"downward")
WINDOW = 6          # words between a noun and its direction word


def _get(url: str) -> str:
    path = CACHE_DIR / "fomc" / (re.sub(r"[^A-Za-z0-9]", "_", url.replace(BASE, "")) + ".html")
    if path.exists():
        return path.read_text()
    for attempt in range(4):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            if r.status_code == 200:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(r.text)
                return r.text
        except requests.RequestException:
            pass
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"cannot fetch {url}")


def statement_urls() -> list[str]:
    pages = [f"{BASE}/monetarypolicy/fomchistorical{y}.htm" for y in range(FIRST_YEAR, 2021)]
    pages.append(f"{BASE}/monetarypolicy/fomccalendars.htm")
    found: dict[str, str] = {}                 # date -> url; two link formats (old pages use /press/monetary/)
    for page in pages:
        try:
            text = _get(page)
        except RuntimeError:
            continue
        for d in re.findall(r"/newsevents/pressreleases/monetary(\d{8})a\.htm", text):
            found[d] = f"{BASE}/newsevents/pressreleases/monetary{d}a.htm"
        for d in re.findall(r"/newsevents/press/monetary/(\d{8})a\.htm", text):
            found.setdefault(d, f"{BASE}/newsevents/press/monetary/{d}a.htm")
    return [found[d] for d in sorted(found) if f"{d[:4]}-{d[4:6]}-{d[6:]}" <= LAST_DAY and int(d[:4]) >= FIRST_YEAR]


def statement_text(page: str) -> str:
    m = re.search(r'<div class="col-xs-12 col-sm-8 col-md-8">(.*?)</div>', page, re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1) if m else ""))).strip()


def is_policy_statement(text: str) -> bool:
    return "Committee" in text and "federal funds rate" in text


def tone(text: str) -> float | None:
    hawk = dove = 0
    for sentence in re.split(r"(?<=[.;])\s+", text.lower()):
        words = sentence.split()
        joined = " ".join(words)
        for noun_re, up_is_hawk in ((HAWK_NOUNS, True), (SLACK_NOUNS, False)):
            for m in re.finditer(rf"\b({noun_re})\b", joined):
                start = len(joined[:m.start()].split())
                lo, hi = max(0, start - WINDOW), start + len(m.group(0).split()) + WINDOW
                around = " ".join(words[lo:hi])
                up = re.search(rf"\b({UP})\b", around) is not None
                down = re.search(rf"\b({DOWN})\b", around) is not None
                if up == down:
                    continue
                if up == up_is_hawk:
                    hawk += 1
                else:
                    dove += 1
    return (hawk - dove) / (hawk + dove) if hawk + dove else None


def ny_open(day: date, hour: int) -> int:
    """UTC ms of the hourly bar that opens at `hour`:00 New York time on `day`."""
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=NEW_YORK).timestamp() * 1000)


def two_weekdays_later(day: date) -> date:
    d, n = day, 0
    while n < 2:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return d


def basket_trade(bars: dict[str, dict[int, dict]], day: date, usd_dir: int) -> float | None:
    """Mean over the USD pairs of the net return of being `usd_dir` USD from 16:00 NY to 16:00 NY two weekdays on."""
    t0, t1 = ny_open(day, 16), ny_open(two_weekdays_later(day), 16)
    rets = []
    for pair in PAIRS:
        a, b = bars[pair].get(t0), bars[pair].get(t1)
        if not a or not b:
            continue
        side = usd_dir * h1.USD_SIGN[pair]              # +1 = buy the pair
        r = b["bid_open"] / a["ask_open"] - 1 if side > 0 else 1 - b["ask_open"] / a["bid_open"]
        rets.append(r - h1.MARKUP)
    return sum(rets) / len(rets) if len(rets) >= 5 else None


def usd_reaction(bars: dict[str, dict[int, dict]], day: date) -> float | None:
    t0, t1 = ny_open(day, 12), ny_open(day, 16)
    moves = []
    for pair in PAIRS:
        a, b = bars[pair].get(t0), bars[pair].get(t1)
        if a and b:
            mid = lambda x: (x["bid_open"] + x["ask_open"]) / 2      # noqa: E731
            moves.append(h1.USD_SIGN[pair] * (mid(b) / mid(a) - 1))
    return sum(moves) / len(moves) if len(moves) >= 5 else None


def evaluate(events: list[dict]) -> dict:
    if not events:
        return {"events": 0, "passes": False, "reasons": ["no events"]}
    net = [e["net"] for e in events]
    gains, losses = sum(x for x in net if x > 0), -sum(x for x in net if x < 0)
    halves = [[e["net"] for e in events if e["day"] < SPLIT], [e["net"] for e in events if e["day"] >= SPLIT]]
    rng = random.Random(11)
    p = sum(sum(rng.choice(net) for _ in net) > 0 for _ in range(4000)) / 4000
    s = {"events": len(net), "hit_rate": round(sum(x > 0 for x in net) / len(net), 3),
         "mean_net_bp": round(sum(net) / len(net) * 1e4, 2), "profit_factor": round(gains / losses, 3) if losses else None,
         "p_mean_positive": p, "halves_mean_bp": [round(sum(x) / len(x) * 1e4, 2) if x else None for x in halves]}
    reasons = []
    if s["events"] < GATE["min_events"]:
        reasons.append("too few events")
    if p < GATE["min_p"]:
        reasons.append(f"P {p:.3f} < {GATE['min_p']}")
    if (s["profit_factor"] or 0) < GATE["min_pf"]:
        reasons.append(f"PF {s['profit_factor']} < {GATE['min_pf']}")
    if not all(halves) or min(sum(x) for x in halves) <= 0:
        reasons.append("not positive in both halves")
    return {**s, "passes": not reasons, "reasons": reasons}


def main() -> None:
    from forex_ai_analyst.forex import dukascopy
    dukascopy.prefetch([(p, date(y, m, 1)) for p in PAIRS for y, m in h1.months() if y >= FIRST_YEAR], hourly=True)
    bars = {p: {b["datetime"]: b for b in h1.load(p)} for p in PAIRS}
    statements = []
    for url in statement_urls():
        text = statement_text(_get(url))
        if is_policy_statement(text):
            d = re.search(r"(\d{8})a\.htm", url).group(1)
            statements.append({"day": f"{d[:4]}-{d[4:6]}-{d[6:]}", "tone": tone(text)})
    ft1, ft2, prev = [], [], None
    for s in statements:
        day = date.fromisoformat(s["day"])
        if s["tone"] is not None and prev is not None and s["tone"] != prev:
            net = basket_trade(bars, day, 1 if s["tone"] > prev else -1)
            if net is not None:
                ft1.append({"day": s["day"], "net": net, "tone": s["tone"]})
        if s["tone"] is not None:
            prev = s["tone"]
        reaction = usd_reaction(bars, day)
        if reaction:
            net = basket_trade(bars, day, 1 if reaction > 0 else -1)
            if net is not None:
                ft2.append({"day": s["day"], "net": net})
    report = {"statements": len(statements), "FT1": evaluate(ft1), "FT2": evaluate(ft2)}
    print(json.dumps(report))
    Path("research_output").mkdir(exist_ok=True)
    Path("research_output/fomc_study.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(), "gate": GATE, "report": report,
         "tones": statements}, indent=2) + "\n")


if __name__ == "__main__":
    main()
