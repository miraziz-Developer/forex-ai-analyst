import logging
import time

import requests

logger = logging.getLogger(__name__)

BINGX_BASE_URL = "https://open-api-vst.bingx.com"
DEPTH_LIMIT = 20  # order book levels each side to sum for the imbalance read

# In-memory history so we can say whether OI is rising/falling, not just its
# current value. Resets on restart — fine, it just means the first cycle
# after any restart has no prior point to compare against.
_oi_history: dict[str, list[tuple[float, float]]] = {}  # symbol -> [(timestamp, oi), ...]
_OI_HISTORY_MAX_AGE_SECONDS = 6 * 3600


def _record_and_compare_oi(symbol: str, current_oi: float) -> str | None:
    now = time.monotonic()
    history = _oi_history.setdefault(symbol, [])
    history[:] = [(t, v) for t, v in history if now - t < _OI_HISTORY_MAX_AGE_SECONDS]

    comparison = None
    if history:
        prev_t, prev_oi = history[0]  # oldest kept point, for the widest useful comparison
        if prev_oi:
            change_pct = (current_oi - prev_oi) / prev_oi * 100
            hours_ago = (now - prev_t) / 3600
            comparison = f"{change_pct:+.2f}% since ~{hours_ago:.1f}h ago"

    history.append((now, current_oi))
    return comparison


def fetch_institutional_context(symbol: str) -> dict:
    """Funding rate, open interest (with trend vs. its own recent history), mark/
    index spread, and order-book bid/ask imbalance — the closest thing to 'smart
    money positioning' data available without a paid data feed. Best-effort: any
    single failure here just means less context, never blocks the analysis."""
    context = {}

    try:
        response = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/quote/premiumIndex",
                                 params={"symbol": symbol}, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("code") == 0:
            d = data["data"]
            context["funding_rate_pct"] = float(d["lastFundingRate"]) * 100
            context["mark_price"] = float(d["markPrice"])
            context["index_price"] = float(d["indexPrice"])
            context["mark_index_spread_pct"] = (
                (context["mark_price"] - context["index_price"]) / context["index_price"] * 100
            )
    except Exception as exc:
        logger.warning("Failed to fetch premium index for %s: %s", symbol, exc)

    try:
        response = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/quote/openInterest",
                                 params={"symbol": symbol}, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("code") == 0:
            oi = float(data["data"]["openInterest"])
            context["open_interest"] = oi
            context["open_interest_trend"] = _record_and_compare_oi(symbol, oi)
    except Exception as exc:
        logger.warning("Failed to fetch open interest for %s: %s", symbol, exc)

    try:
        response = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/quote/depth",
                                 params={"symbol": symbol, "limit": DEPTH_LIMIT}, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("code") == 0:
            bid_volume = sum(float(qty) for _, qty in data["data"]["bids"])
            ask_volume = sum(float(qty) for _, qty in data["data"]["asks"])
            total = bid_volume + ask_volume
            if total:
                context["orderbook_bid_pct"] = bid_volume / total * 100
                context["orderbook_ask_pct"] = ask_volume / total * 100
    except Exception as exc:
        logger.warning("Failed to fetch order book depth for %s: %s", symbol, exc)

    return context


def format_institutional_context(context: dict) -> str:
    if not context:
        return "Institutional data: unavailable this cycle."
    lines = ["Institutional / positioning data:"]
    if "funding_rate_pct" in context:
        lines.append(f"- Current funding rate: {context['funding_rate_pct']:.4f}% "
                      f"(positive = longs pay shorts = crowd leaning long; "
                      f"negative = crowd leaning short)")
    if "open_interest" in context:
        trend = context.get("open_interest_trend") or "no prior reading yet to compare"
        lines.append(f"- Open interest: {context['open_interest']:,.0f} contracts (change: {trend})")
    if "mark_index_spread_pct" in context:
        lines.append(f"- Mark/index price spread: {context['mark_index_spread_pct']:.4f}%")
    if "orderbook_bid_pct" in context:
        lines.append(
            f"- Order book imbalance (top {DEPTH_LIMIT} levels each side): "
            f"{context['orderbook_bid_pct']:.1f}% bid volume / {context['orderbook_ask_pct']:.1f}% ask volume "
            f"({'more resting buy interest' if context['orderbook_bid_pct'] > 55 else 'more resting sell interest' if context['orderbook_ask_pct'] > 55 else 'roughly balanced'})"
        )
    return "\n".join(lines)
