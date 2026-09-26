"""Point-in-time features (15-minute decision bars).

A bar's decision time is its close. Every feature at bar i uses only records
whose available_time_ms <= that decision time, and every percentile ranks the
current value only among observations from the preceding 30 days (current
included) - never the whole dataset. Missing inputs give None, never 0.

Feature versions differ only in how percentiles treat ties:
  v1 ranks a value as the share of the window <= it. Funding sits at the same
     baseline rate for long stretches, so under v1 the ordinary baseline scores
     as an extreme high and a low extreme is almost unreachable (found after the
     crowding_exhaustion_v2 event study; kept unchanged so v2 stays reproducible).
  v2 uses the mid-rank (share below plus half the ties), so a flat series sits
     near 50 and only genuinely unusual values reach the tails.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from collections import deque
from dataclasses import dataclass, field

from forex_ai_analyst.research.edge_lab.models import RawRecord

FEATURE_VERSION = "v1"   # default, kept for registered v1-feature hypotheses
TIE_RULE = {"v1": "right", "v2": "mid"}
BAR_MS = 900_000
DAY_MS = 86_400_000
WINDOW_BARS = 30 * 96
WINDOW_MS = 30 * DAY_MS
OI_STALE_MS = 30 * 60_000
FLOW_BARS = 4


@dataclass
class Frame:
    pair: str
    open_time: list[int]
    decision_time: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    quote_volume: list[float]
    taker_buy_quote: list[float]
    spot_high: list[float | None]
    spot_low: list[float | None]
    premium: list[float | None]
    oi: list[float | None]
    funding_pct: list[float | None]
    features: dict[str, list] = field(default_factory=dict)
    feature_version: str = FEATURE_VERSION

    def __len__(self) -> int:
        return len(self.open_time)


def _rank(ordered: list[float], value: float, ties: str) -> float:
    if ties == "mid":
        return 100.0 * (bisect_left(ordered, value) + bisect_right(ordered, value)) / 2 / len(ordered)
    return 100.0 * bisect_right(ordered, value) / len(ordered)


def rolling_percentile(values: list[float | None], window: int, min_obs: int | None = None,
                       ties: str = "right") -> list[float | None]:
    """Percentile of current among the last `window` bars' non-None values (current included).

    ties="right": share <= current (feature v1). ties="mid": mid-rank (feature v2)."""
    min_obs = window // 2 if min_obs is None else min_obs
    out: list[float | None] = [None] * len(values)
    ordered: list[float] = []
    queue: deque = deque()
    for i, value in enumerate(values):
        if value is not None:
            queue.append((i, value))
            insort(ordered, value)
        while queue and queue[0][0] <= i - window:
            _, old = queue.popleft()
            del ordered[bisect_left(ordered, old)]
        if value is not None and len(ordered) >= min_obs:
            out[i] = _rank(ordered, value, ties)
    return out


def event_percentiles(events: list[tuple[int, float]], window_ms: int, min_obs: int,
                      ties: str = "right") -> list[float | None]:
    """Percentile of each event's value among events in (t - window, t] (for sparse series like funding)."""
    out, ordered, queue = [], [], deque()
    for stamp, value in events:
        queue.append((stamp, value))
        insort(ordered, value)
        while queue and queue[0][0] <= stamp - window_ms:
            _, old = queue.popleft()
            del ordered[bisect_left(ordered, old)]
        out.append(_rank(ordered, value, ties) if len(ordered) >= min_obs else None)
    return out


def _as_of(decision_times: list[int], records: list[RawRecord], key: str, max_age_ms: int | None = None) -> list:
    """Latest value available at each decision time; None once it is older than max_age_ms."""
    out, j, current, current_event = [], 0, None, None
    ordered = sorted(records, key=lambda r: r.available_time_ms)
    for decision in decision_times:
        while j < len(ordered) and ordered[j].available_time_ms <= decision:
            current, current_event = ordered[j].values.get(key), ordered[j].event_time_ms
            j += 1
        stale = max_age_ms is not None and current_event is not None and decision - current_event > max_age_ms
        out.append(None if stale else current)
    return out


def align(pair: str, perp: list[RawRecord], spot: list[RawRecord], premium: list[RawRecord],
          funding: list[RawRecord], oi: list[RawRecord], feature_version: str = FEATURE_VERSION) -> Frame:
    """Build one decision grid from normalized (sorted, de-duplicated) records."""
    open_time = [r.event_time_ms for r in perp]
    decision = [r.available_time_ms for r in perp]
    spot_by_time = {r.event_time_ms: r.values for r in spot}
    premium_by_time = {r.event_time_ms: r.values.get("premium") for r in premium}
    funding_events = [(r.available_time_ms, r.values["rate"]) for r in sorted(funding, key=lambda r: r.available_time_ms)
                      if r.values.get("rate") is not None]
    percentiles = event_percentiles(funding_events, WINDOW_MS, min_obs=30, ties=TIE_RULE[feature_version])
    funding_pct, j, current = [], 0, None
    for t in decision:
        while j < len(funding_events) and funding_events[j][0] <= t:
            current = percentiles[j]
            j += 1
        funding_pct.append(current)
    return Frame(
        pair=pair, open_time=open_time, decision_time=decision,
        open=[r.values["open"] for r in perp], high=[r.values["high"] for r in perp],
        low=[r.values["low"] for r in perp], close=[r.values["close"] for r in perp],
        quote_volume=[r.values["quote_volume"] for r in perp], taker_buy_quote=[r.values["taker_buy_quote"] for r in perp],
        spot_high=[spot_by_time.get(t, {}).get("high") for t in open_time],
        spot_low=[spot_by_time.get(t, {}).get("low") for t in open_time],
        premium=[premium_by_time.get(t) for t in open_time],
        oi=_as_of(decision, oi, "oi", OI_STALE_MS),
        funding_pct=funding_pct, feature_version=feature_version,
    )


def prior_extreme(series: list[float | None], n: int, highest: bool) -> list[float | None]:
    """max/min of series[i-n:i] (strictly prior bars); None while that window has a gap."""
    out: list[float | None] = [None] * len(series)
    q: deque = deque()
    last_missing = -(10 ** 12)
    for i, value in enumerate(series):
        while q and q[0] < i - n:
            q.popleft()
        if i >= n and last_missing < i - n and q:
            out[i] = series[q[0]]
        if value is None:
            last_missing = i
            continue
        while q and ((series[q[-1]] <= value) if highest else (series[q[-1]] >= value)):
            q.pop()
        q.append(i)
    return out


def _change(a, b):
    return a / b - 1 if a is not None and b not in (None, 0) else None


def compute(frame: Frame, lookbacks: tuple[int, ...] = (20, 55)) -> Frame:
    n, f = len(frame), frame.features
    ties = TIE_RULE[frame.feature_version]
    c, qv, tb = frame.close, frame.quote_volume, frame.taker_buy_quote
    for name, bars in (("1h", 4), ("4h", 16)):
        change = [_change(frame.oi[i], frame.oi[i - bars]) if i >= bars else None for i in range(n)]
        f[f"oi_chg_{name}"] = change
        f[f"oi_chg_{name}_pct"] = rolling_percentile(change, WINDOW_BARS, ties=ties)
    for bars in lookbacks:
        f[f"prior_high_{bars}"] = prior_extreme(frame.high, bars, True)
        f[f"prior_low_{bars}"] = prior_extreme(frame.low, bars, False)
        f[f"spot_prior_high_{bars}"] = prior_extreme(frame.spot_high, bars, True)
        f[f"spot_prior_low_{bars}"] = prior_extreme(frame.spot_low, bars, False)
    buy_ratio, sell_ratio, up_impact, down_impact, ret4 = ([None] * n for _ in range(5))
    for i in range(FLOW_BARS, n):
        quote = sum(qv[i - FLOW_BARS + 1:i + 1])
        buy = sum(tb[i - FLOW_BARS + 1:i + 1])
        sell = quote - buy
        r = c[i] / c[i - FLOW_BARS] - 1
        ret4[i] = r
        if quote > 0:
            buy_ratio[i], sell_ratio[i] = buy / quote, sell / quote
        if buy > 0:
            up_impact[i] = r / buy          # price progress per unit of aggressive buying
        if sell > 0:
            down_impact[i] = -r / sell      # downside progress per unit of aggressive selling
    f["ret4"], f["buy_ratio4"], f["sell_ratio4"] = ret4, buy_ratio, sell_ratio
    f["buy_ratio4_pct"] = rolling_percentile(buy_ratio, WINDOW_BARS, ties=ties)
    f["sell_ratio4_pct"] = rolling_percentile(sell_ratio, WINDOW_BARS, ties=ties)
    f["up_impact_pct"] = rolling_percentile(up_impact, WINDOW_BARS, ties=ties)
    f["down_impact_pct"] = rolling_percentile(down_impact, WINDOW_BARS, ties=ties)
    f["premium_pct"] = rolling_percentile(frame.premium, WINDOW_BARS, ties=ties)
    f["funding_pct"] = frame.funding_pct
    return frame
