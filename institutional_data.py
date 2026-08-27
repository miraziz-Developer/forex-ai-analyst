import logging

import requests

logger = logging.getLogger(__name__)

BINGX_BASE_URL = "https://open-api-vst.bingx.com"


def fetch_institutional_context(symbol: str) -> dict:
    """Funding rate, open interest, and mark/index spread — the closest thing to
    'smart money positioning' data available without a paid data feed. Best-effort:
    any failure here just means less context, never blocks the analysis."""
    context = {}

    try:
        response = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/quote/premiumIndex",
                                 params={"symbol": symbol}, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("code") == 0:
            d = data["data"]
            context["funding_rate_pct"] = float(d["lastFundingRate"]) * 100
            context["next_funding_time"] = d["nextFundingTime"]
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
            context["open_interest"] = float(data["data"]["openInterest"])
    except Exception as exc:
        logger.warning("Failed to fetch open interest for %s: %s", symbol, exc)

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
        lines.append(f"- Open interest: {context['open_interest']:,.0f} contracts")
    if "mark_index_spread_pct" in context:
        lines.append(f"- Mark/index price spread: {context['mark_index_spread_pct']:.4f}%")
    return "\n".join(lines)
