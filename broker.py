import hashlib
import hmac
import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

BINGX_API_KEY = os.environ.get("BINGX_API_KEY", "")
BINGX_SECRET = os.environ.get("BINGX_SECRET", "")

# Hardcoded on purpose: this is the demo/VST (virtual money) domain. There is
# deliberately no env var or code path to point this at open-api.bingx.com
# (the real-money domain) — switching to live trading is a separate, manual
# code change, never a config flip.
BASE_URL = "https://open-api-vst.bingx.com"

DEFAULT_LEVERAGE = int(os.environ.get("LEVERAGE", "3"))

# BingX requires quantity rounded to each contract's precision; hardcoded for
# our small fixed pair set rather than an extra API call per order.
QUANTITY_PRECISION = {"BTC-USDT": 4, "ETH-USDT": 3, "SOL-USDT": 2, "XRP-USDT": 0, "BNB-USDT": 2}


class BingXApiError(RuntimeError):
    """A safe account/API failure description which deliberately excludes secrets."""

    def __init__(self, path: str, *, http_status: int | None = None, code=None,
                 message: str | None = None, category: str = "unknown"):
        self.diagnostic = {
            "endpoint": path,
            "http_status": http_status,
            "bingx_code": code,
            "bingx_msg": str(message or "").replace("\n", " ")[:240] or None,
            "category": category,
        }
        super().__init__(f"BingX {category} failure on {path}")


def _error_category(http_status: int | None, code, message: str | None) -> str:
    text = str(message or "").lower()
    if "signature" in text or "sign" in text:
        return "signature"
    if "timestamp" in text or "recvwindow" in text or "clock" in text:
        return "clock"
    if "ip" in text and ("white" in text or "restrict" in text or "forbid" in text):
        return "ip_whitelist"
    if "permission" in text or "authorize" in text or "forbidden" in text or http_status == 403:
        return "permission"
    if "api key" in text or "apikey" in text or "credential" in text or http_status == 401:
        return "credentials"
    if http_status is None:
        return "transport"
    return "api_response"


def _signed_request(method: str, path: str, params: dict) -> dict:
    params = dict(params)
    params["timestamp"] = str(int(time.time() * 1000))
    query = "&".join(f"{k}={v}" for k, v in params.items())
    signature = hmac.new(BINGX_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query}&signature={signature}"
    try:
        response = requests.request(method, url, headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=15)
    except requests.RequestException as exc:
        raise BingXApiError(path, message=type(exc).__name__, category="transport") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise BingXApiError(path, http_status=response.status_code, message="non-JSON response",
                            category=_error_category(response.status_code, None, None)) from exc
    if response.status_code >= 400:
        message = data.get("msg") if isinstance(data, dict) else None
        code = data.get("code") if isinstance(data, dict) else None
        raise BingXApiError(path, http_status=response.status_code, code=code, message=message,
                            category=_error_category(response.status_code, code, message))
    if not isinstance(data, dict):
        raise BingXApiError(path, http_status=response.status_code, message="invalid JSON payload", category="api_response")
    if data.get("code") != 0:
        raise BingXApiError(path, http_status=response.status_code, code=data.get("code"), message=data.get("msg"),
                            category=_error_category(response.status_code, data.get("code"), data.get("msg")))
    return data


def round_quantity(symbol: str, raw_quantity: float) -> float:
    precision = QUANTITY_PRECISION.get(symbol, 4)
    return round(raw_quantity, precision)


def get_vst_usdt_balance() -> dict:
    """Return normalized USDT account values from the VST account only.

    The exchange has returned the balance row both directly and nested under
    ``data.balance`` across API revisions.  Do not return the raw payload: this
    value is passed to the AI context and must never include account metadata.
    """
    data = _signed_request("GET", "/openApi/swap/v2/user/balance", {})
    value = data.get("data", {})
    rows = value.get("balance", value) if isinstance(value, dict) else value
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise RuntimeError("BingX VST balance query returned an invalid payload")

    row = next((item for item in rows if isinstance(item, dict) and
                str(item.get("asset", item.get("currency", "USDT"))).upper() == "USDT"), None)
    if row is None:
        raise RuntimeError("BingX VST balance query did not return a USDT balance")

    def number(*names: str) -> float | None:
        for name in names:
            try:
                raw = row.get(name)
                if raw is not None and raw != "":
                    return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    equity = number("equity", "balance", "totalBalance")
    available = number("availableMargin", "availableBalance", "available")
    unrealized = number("unrealizedProfit", "unrealizedPnl")
    if equity is None or available is None:
        raise RuntimeError("BingX VST balance query omitted equity or available margin")
    return {"equity_usdt": equity, "available_usdt": available,
            "unrealized_pnl_usdt": unrealized}


def set_leverage(symbol: str, position_side: str, leverage: int = DEFAULT_LEVERAGE) -> None:
    _signed_request("POST", "/openApi/swap/v2/trade/leverage",
                     {"symbol": symbol, "side": position_side, "leverage": str(leverage)})


def place_market_order(symbol: str, direction: str, quantity: float,
                        take_profit_price: float, stop_loss_price: float, *, leverage: int = DEFAULT_LEVERAGE) -> dict:
    """direction: 'BUY' opens/adds to a LONG, 'SELL' opens/adds to a SHORT."""
    position_side = "LONG" if direction == "BUY" else "SHORT"
    if not 1 <= int(leverage) <= 125:
        raise ValueError("BingX VST leverage must be 1..125")
    set_leverage(symbol, position_side, int(leverage))

    take_profit = {"type": "TAKE_PROFIT_MARKET", "stopPrice": take_profit_price, "workingType": "MARK_PRICE"}
    stop_loss = {"type": "STOP_MARKET", "stopPrice": stop_loss_price, "workingType": "MARK_PRICE"}

    data = _signed_request("POST", "/openApi/swap/v2/trade/order", {
        "symbol": symbol,
        "side": direction,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": str(quantity),
        "takeProfit": json.dumps(take_profit),
        "stopLoss": json.dumps(stop_loss),
    })
    order = data["data"]["order"]
    return {"order_id": str(order["orderId"]), "fill_price": float(order["avgPrice"])}


def get_position(symbol: str, position_side: str) -> dict | None:
    """Returns the open position dict for this symbol+side, or None if it's been closed
    (by TP, SL, liquidation, or manual close)."""
    data = _signed_request("GET", "/openApi/swap/v2/user/positions", {"symbol": symbol})
    for position in data.get("data", []):
        if position["positionSide"] == position_side and float(position["positionAmt"]) != 0:
            return position
    return None


def close_position(symbol: str, direction: str, quantity: float) -> dict:
    """direction is the ORIGINAL entry direction (e.g. 'BUY' for a LONG we're now closing).
    In hedge mode, closing means an opposite-side order on the same positionSide, no
    reduceOnly/closePosition flag (BingX rejects both on this account type)."""
    position_side = "LONG" if direction == "BUY" else "SHORT"
    close_side = "SELL" if direction == "BUY" else "BUY"
    data = _signed_request("POST", "/openApi/swap/v2/trade/order", {
        "symbol": symbol,
        "side": close_side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": str(quantity),
    })
    order = data["data"]["order"]
    return {"order_id": str(order["orderId"]), "fill_price": float(order["avgPrice"])}


def get_order(symbol: str, order_id: str) -> dict:
    """Fetch one VST order by its immutable exchange order ID.

    The response is intentionally retained as broker evidence rather than used
    to infer account-level income. BingX has changed field casing between API
    revisions, so normalization is conservative and preserves absent values.
    """
    data = _signed_request("GET", "/openApi/swap/v2/trade/order",
                           {"symbol": symbol, "orderId": str(order_id)})
    value = data.get("data", {})
    order = value.get("order", value) if isinstance(value, dict) else {}
    if not isinstance(order, dict):
        raise RuntimeError("BingX order query returned an invalid payload")

    def number(*names: str) -> float | None:
        for name in names:
            try:
                value = order.get(name)
                if value is not None and value != "":
                    return float(value)
            except (TypeError, ValueError):
                continue
        return None

    return {
        "order_id": str(order.get("orderId", order_id)),
        "status": str(order.get("status", "")).upper(),
        "fill_price": number("avgPrice", "averagePrice", "price"),
        # These remain None unless the order endpoint explicitly binds them to
        # this order; account income is never guessed or allocated here.
        "commission_usdt": number("commission", "fee"),
        "realized_pnl_usdt": number("realizedProfit", "realizedPnl"),
        "raw": order,
    }


def income_history(symbol: str, start_time_ms: int) -> list[dict]:
    """Documented V2 income endpoint; callers parse fields defensively."""
    data = _signed_request("GET", "/openApi/swap/v2/user/income",
                           {"symbol": symbol, "startTime": str(start_time_ms), "limit": "100"})
    values = data.get("data", [])
    return values if isinstance(values, list) else values.get("list", [])


def vst_income_summary(start_time_ms: int) -> dict:
    """Return BingX-reported VST income totals for the configured account.

    Income is account-scoped, not reliably attributable to an originating
    strategy order ID, so it must remain separate from the local AI journal.
    """
    totals = {"realized_pnl_usdt": 0.0, "fees_usdt": 0.0, "funding_usdt": 0.0, "entries": 0}
    for symbol in QUANTITY_PRECISION:
        for item in income_history(symbol, start_time_ms):
            kind = str(item.get("incomeType", item.get("type", ""))).upper()
            try:
                value = float(item.get("income", item.get("amount")))
            except (TypeError, ValueError):
                continue
            totals["entries"] += 1
            if "FUNDING" in kind:
                totals["funding_usdt"] += value
            elif "COMMISSION" in kind or "FEE" in kind:
                totals["fees_usdt"] += abs(value)
            elif "REALIZED" in kind or "PNL" in kind:
                totals["realized_pnl_usdt"] += value
    totals["net_pnl_usdt"] = totals["realized_pnl_usdt"] - totals["fees_usdt"] + totals["funding_usdt"]
    return totals
