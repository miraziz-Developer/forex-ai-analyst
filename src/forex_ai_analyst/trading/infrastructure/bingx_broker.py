import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# Hardcoded on purpose: this is the demo/VST (virtual money) domain. There is
# deliberately no env var or code path to point this at open-api.bingx.com
# (the real-money domain) — switching to live trading is a separate, manual
# code change, never a config flip.
BASE_URL = "https://open-api-vst.bingx.com"

DEFAULT_LEVERAGE = int(os.environ.get("LEVERAGE", "3"))

# BingX requires quantity rounded to each contract's precision; hardcoded for
# our small fixed pair set rather than an extra API call per order.
# From BingX /openApi/swap/v2/quote/contracts (quantityPrecision, tradeMinQuantity), checked 2026-09-26.
QUANTITY_PRECISION = {"BTC-USDT": 4, "ETH-USDT": 2, "SOL-USDT": 2, "XRP-USDT": 0, "BNB-USDT": 2,
                      "DOGE-USDT": 0, "ADA-USDT": 0, "LINK-USDT": 1, "AVAX-USDT": 0, "LTC-USDT": 1,
                      "DOT-USDT": 1, "TRX-USDT": 0, "BCH-USDT": 2, "UNI-USDT": 0, "NEAR-USDT": 0,
                      "ATOM-USDT": 2, "ETC-USDT": 2, "FIL-USDT": 1, "AAVE-USDT": 1, "XLM-USDT": 0}
MIN_QUANTITY = {"BTC-USDT": 0.0001, "ETH-USDT": 0.01, "SOL-USDT": 0.02, "XRP-USDT": 2, "BNB-USDT": 0.01,
                "DOGE-USDT": 21, "ADA-USDT": 8, "LINK-USDT": 0.2, "AVAX-USDT": 1, "LTC-USDT": 0.1,
                "DOT-USDT": 1.7, "TRX-USDT": 6, "BCH-USDT": 0.01, "UNI-USDT": 1, "NEAR-USDT": 1,
                "ATOM-USDT": 1.09, "ETC-USDT": 0.21, "FIL-USDT": 1.9, "AAVE-USDT": 0.1, "XLM-USDT": 10}
_HISTORY_WINDOW_MS = 7 * 24 * 60 * 60 * 1000


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
    # Read live, not cached at import: readiness/status checks elsewhere (e.g.
    # http.trade_readiness) also read these env vars fresh on every call, and a
    # key added/rotated without a full process restart must take effect here too.
    api_key = os.environ.get("BINGX_API_KEY", "")
    secret = os.environ.get("BINGX_SECRET", "")
    params = dict(params)
    params["timestamp"] = str(int(time.time() * 1000))
    query = "&".join(f"{k}={v}" for k, v in params.items())
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query}&signature={signature}"
    try:
        response = requests.request(method, url, headers={"X-BX-APIKEY": api_key}, timeout=15)
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
    """Round down to the contract's step; 0 when below BingX's minimum order size."""
    precision = QUANTITY_PRECISION.get(symbol, 4)
    step = 10 ** -precision
    quantity = round(int(raw_quantity / step + 1e-9) * step, precision)   # never round risk up
    return quantity if quantity >= MIN_QUANTITY.get(symbol, 0) else 0.0


def get_vst_usdt_balance() -> dict:
    """Return normalized USDT account values from the VST account only.

    The exchange has returned the balance row both directly and nested under
    ``data.balance`` across API revisions.  Do not return the raw payload: this
    value feeds risk sizing and /health and must never include account metadata.
    """
    path = "/openApi/swap/v2/user/balance"
    data = _signed_request("GET", path, {})
    value = data.get("data", {})
    rows = value.get("balance", value) if isinstance(value, dict) else value
    if isinstance(rows, dict) and isinstance(rows.get("USDT"), dict):
        # Some account APIs key balances by the asset instead of putting the
        # asset name in every row.  Normalize that documented-style mapping
        # without treating arbitrary unknown balances as USDT.
        rows = [{**rows["USDT"], "asset": "USDT"}]
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise BingXApiError(path, code=data.get("code"), message="balance payload is not a list or object",
                            category="account_schema")

    asset_fields = ("asset", "currency", "coin", "currencyName", "assetName", "symbol")

    def asset_name(item: dict) -> str:
        for field in asset_fields:
            value = item.get(field)
            if value is not None and str(value).strip():
                return str(value).strip().upper()
        # Swap V2's single balance object can omit an asset label because the
        # endpoint itself is USDT-margined.  This fallback applies only to one
        # object, never to an unlabelled item in a multi-asset response.
        return "USDT" if len(rows) == 1 else ""

    def is_usdt_margin_row(item: dict) -> bool:
        asset = asset_name(item)
        if asset == "USDT":
            return True
        # The VST demo Swap endpoint currently labels its one USD-margined
        # account row as VST.  Its equity/availableMargin are the actual demo
        # margin values used by this same Swap order endpoint.  Do not apply
        # this exception to a multi-asset response or to any other asset.
        return asset == "VST" and len(rows) == 1

    schema = {
        "row_count": len(rows),
        "row_fields": sorted({str(key) for item in rows if isinstance(item, dict) for key in item})[:24],
        "asset_labels": sorted({asset_name(item) for item in rows if isinstance(item, dict) and asset_name(item)})[:12],
    }
    row = next((item for item in rows if isinstance(item, dict) and is_usdt_margin_row(item)), None)
    if row is None:
        error = BingXApiError(path, code=data.get("code"), message="USDT balance row missing", category="account_schema")
        error.diagnostic["balance_schema"] = schema
        raise error

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
        error = BingXApiError(path, code=data.get("code"), message="USDT equity or available margin missing",
                              category="account_schema")
        error.diagnostic["balance_schema"] = schema
        raise error
    return {"equity_usdt": equity, "available_usdt": available,
            "unrealized_pnl_usdt": unrealized}


def set_leverage(symbol: str, position_side: str, leverage: int = DEFAULT_LEVERAGE) -> None:
    _signed_request("POST", "/openApi/swap/v2/trade/leverage",
                     {"symbol": symbol, "side": position_side, "leverage": str(leverage)})


def _executed_quantity(order: dict) -> float | None:
    """BingX's own executed-quantity field, when present.

    It can differ from the requested quantity (precision rounding on BingX's
    side, or a partial fill), so callers must prefer this over the requested
    amount when journaling what the broker actually holds - trusting the
    requested amount instead is exactly what causes a later
    'broker quantity smaller than journal quantity' recovery alert.
    """
    for name in ("executedQty", "cumQty", "dealQty", "origQty"):
        try:
            raw = order.get(name)
            if raw is not None and raw != "":
                return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def place_market_order(symbol: str, direction: str, quantity: float,
                        take_profit_price: float | None, stop_loss_price: float, *,
                        leverage: int = DEFAULT_LEVERAGE) -> dict:
    """direction: 'BUY' opens/adds to a LONG, 'SELL' opens/adds to a SHORT.

    take_profit_price=None places only the exchange-side stop loss (trend
    strategies exit on a channel break instead of a fixed target).
    """
    position_side = "LONG" if direction == "BUY" else "SHORT"
    if not 1 <= int(leverage) <= 125:
        raise ValueError("BingX VST leverage must be 1..125")
    set_leverage(symbol, position_side, int(leverage))

    stop_loss = {"type": "STOP_MARKET", "stopPrice": stop_loss_price, "workingType": "MARK_PRICE"}
    params = {
        "symbol": symbol,
        "side": direction,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": str(quantity),
        "stopLoss": json.dumps(stop_loss),
    }
    if take_profit_price is not None:
        params["takeProfit"] = json.dumps({"type": "TAKE_PROFIT_MARKET", "stopPrice": take_profit_price,
                                           "workingType": "MARK_PRICE"})
    data = _signed_request("POST", "/openApi/swap/v2/trade/order", params)
    order = data["data"]["order"]
    return {"order_id": str(order["orderId"]), "fill_price": float(order["avgPrice"]),
            "filled_quantity": _executed_quantity(order)}


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
    return {"order_id": str(order["orderId"]), "fill_price": float(order["avgPrice"]),
            "filled_quantity": _executed_quantity(order)}


def cancel_stop_orders(symbol: str, position_side: str) -> int:
    """Cancel open stop orders left on one position side after a manual close.

    A stop attached to an entry order can outlive a market close; if it stayed,
    it could later close a new position on the same pair at a stale level.
    Returns the number of orders cancelled.
    """
    data = _signed_request("GET", "/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
    value = data.get("data", {})
    orders = value.get("orders", []) if isinstance(value, dict) else value
    cancelled = 0
    for order in orders if isinstance(orders, list) else []:
        if str(order.get("type", "")).upper() in {"STOP_MARKET", "STOP"} and order.get("positionSide") == position_side:
            _signed_request("DELETE", "/openApi/swap/v2/trade/order", {"symbol": symbol, "orderId": str(order["orderId"])})
            cancelled += 1
    return cancelled


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

    result = {
        "order_id": str(order.get("orderId", order_id)),
        "status": str(order.get("status", "")).upper(),
        "fill_price": number("avgPrice", "averagePrice", "price"),
        # realized_pnl_usdt remains None unless the order endpoint explicitly
        # binds it to this order; account income is never guessed or
        # allocated here. commission_usdt is best-effort enriched below from
        # allFillOrders, which this single-order query often omits it from.
        "commission_usdt": number("commission", "fee"),
        "realized_pnl_usdt": number("realizedProfit", "realizedPnl"),
        "raw": order,
    }
    if result["commission_usdt"] is None:
        created_at_ms = number("time", "createTime", "createdTime", "updateTime")
        if created_at_ms is not None:
            try:
                fills = [fill for fill in fill_history(symbol, start_time_ms=int(created_at_ms) - 60_000)
                        if fill["order_id"] == result["order_id"] and fill["commission_usdt"] is not None]
            except Exception as exc:
                logger.warning("BingX order-level fill enrichment unavailable for %s/%s: %s",
                               symbol, result["order_id"], type(exc).__name__)
                fills = []
            if fills:
                result["commission_usdt"] = sum(fill["commission_usdt"] for fill in fills)
    return result


def fill_history(symbol: str, *, start_time_ms: int, end_time_ms: int | None = None) -> list[dict]:
    """Return immutable VST execution fills, keyed by their exchange order ID.

    Unlike an account-income record, every returned fill is explicitly linked to
    one broker ``orderId``.  It can therefore only enrich an order which was
    independently matched by its symbol, side and position side.
    """
    end_time_ms = int(end_time_ms or time.time() * 1000)
    data = _signed_request("GET", "/openApi/swap/v2/trade/allFillOrders", {
        "symbol": symbol, "tradingUnit": "CONT", "startTs": str(int(start_time_ms)),
        "endTs": str(end_time_ms),
    })
    values = data.get("data", [])
    if isinstance(values, dict):
        values = values.get("orders", values.get("list", []))
    if not isinstance(values, list):
        raise RuntimeError("BingX fill history returned an invalid payload")

    def number(fill: dict, *names: str) -> float | None:
        for name in names:
            try:
                raw = fill.get(name)
                if raw is not None and raw != "":
                    return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    def timestamp_ms(fill: dict) -> float | None:
        value = number(fill, "filledTime", "filledTm", "time", "updateTime")
        if value is not None:
            return value
        for name in ("filledTm", "filledTime"):
            raw = fill.get(name)
            if not isinstance(raw, str):
                continue
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000
            except ValueError:
                continue
        return None

    normalized = []
    for fill in values:
        if not isinstance(fill, dict) or fill.get("orderId") is None:
            continue
        normalized.append({
            "order_id": str(fill["orderId"]),
            "fill_price": number(fill, "price", "avgPrice"),
            "filled_quantity": number(fill, "volume", "executedQty", "quantity"),
            "filled_at_ms": timestamp_ms(fill),
            "commission_usdt": number(fill, "commission", "fee"),
            "raw": fill,
        })
    return normalized


def order_history(symbol: str, *, start_time_ms: int, limit: int = 1000) -> list[dict]:
    """Return normalized, immutable VST order-history evidence for one symbol.

    BingX's V2 response has used both ``data.orders`` and ``data`` list shapes.
    Missing order-level economic fields deliberately remain ``None``: they must
    not be replaced with account-wide income values.
    """
    safe_limit = min(max(int(limit), 1), 1000)
    end_time_ms = int(time.time() * 1000)
    # BingX documents a seven-day maximum allOrders time range.  The recovery
    # job is looking for a current close, so querying the recent valid window
    # avoids an API rejection for a long-lived open journal without relaxing
    # the later check against the row's original opened-at time.
    query_start_ms = max(int(start_time_ms), end_time_ms - _HISTORY_WINDOW_MS)
    data = _signed_request("GET", "/openApi/swap/v2/trade/allOrders", {
        "symbol": symbol, "startTime": str(query_start_ms), "endTime": str(end_time_ms), "limit": str(safe_limit),
    })
    value = data.get("data", [])
    if isinstance(value, dict):
        values = value.get("orders", value.get("list", []))
    else:
        values = value
    if not isinstance(values, list):
        raise RuntimeError("BingX order history returned an invalid payload")

    def number(order: dict, *names: str) -> float | None:
        for name in names:
            try:
                raw = order.get(name)
                if raw is not None and raw != "":
                    return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    normalized = []
    for order in values:
        if not isinstance(order, dict):
            continue
        order_id = order.get("orderId")
        if order_id is None:
            continue
        normalized.append({
            "order_id": str(order_id),
            "symbol": str(order.get("symbol", symbol)).upper(),
            "status": str(order.get("status", "")).upper(),
            "side": str(order.get("side", "")).upper(),
            "position_side": str(order.get("positionSide", "")).upper(),
            "type": str(order.get("type", order.get("orderType", ""))).upper(),
            "fill_price": number(order, "avgPrice", "averagePrice", "price"),
            "filled_quantity": number(order, "executedQty", "cumQty", "dealQty", "quantity", "origQty"),
            "created_at_ms": number(order, "time", "createTime", "createdTime", "updateTime"),
            "commission_usdt": number(order, "commission", "fee"),
            "realized_pnl_usdt": number(order, "realizedProfit", "realizedPnl"),
            "raw": order,
        })
    # TP/SL trigger orders can expose their trigger price in ``allOrders`` but
    # omit or delay their actual execution price.  Enrich only by the immutable
    # order ID; never use a symbol/time-only fill as evidence for a journal row.
    try:
        fills_by_order: dict[str, list[dict]] = {}
        for fill in fill_history(symbol, start_time_ms=query_start_ms, end_time_ms=end_time_ms):
            fills_by_order.setdefault(fill["order_id"], []).append(fill)
        for order in normalized:
            fills = fills_by_order.get(order["order_id"], [])
            priced = [fill for fill in fills if fill["fill_price"] is not None and fill["filled_quantity"] is not None]
            quantity = sum(float(fill["filled_quantity"]) for fill in priced)
            if quantity > 0:
                order["fill_price"] = sum(float(fill["fill_price"]) * float(fill["filled_quantity"]) for fill in priced) / quantity
                order["filled_quantity"] = quantity
            timestamps = [float(fill["filled_at_ms"]) for fill in fills if fill["filled_at_ms"] is not None]
            if timestamps:
                order["created_at_ms"] = max(timestamps)
            commissions = [float(fill["commission_usdt"]) for fill in fills if fill["commission_usdt"] is not None]
            if commissions:
                order["commission_usdt"] = sum(commissions)
    except Exception as exc:
        # The order endpoint remains authoritative for matching metadata.  A
        # temporary fill-history failure must not invent an exit or turn a
        # known order-history response into a false broker outage.
        logger.warning("BingX VST fill-history enrichment unavailable for %s: %s", symbol, type(exc).__name__)
    return normalized


def income_history(symbol: str, start_time_ms: int) -> list[dict]:
    """Documented V2 income endpoint; callers parse fields defensively."""
    data = _signed_request("GET", "/openApi/swap/v2/user/income",
                           {"symbol": symbol, "startTime": str(start_time_ms), "limit": "100"})
    values = data.get("data", [])
    return values if isinstance(values, list) else values.get("list", [])


def vst_income_summary(start_time_ms: int) -> dict:
    """Return BingX-reported VST income totals for the configured account.

    Income is account-scoped, not reliably attributable to an originating
    strategy order ID, so it must remain separate from the local strategy journal.
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
