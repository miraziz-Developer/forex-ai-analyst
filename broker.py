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


def _signed_request(method: str, path: str, params: dict) -> dict:
    params = dict(params)
    params["timestamp"] = str(int(time.time() * 1000))
    query = "&".join(f"{k}={v}" for k, v in params.items())
    signature = hmac.new(BINGX_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query}&signature={signature}"
    response = requests.request(method, url, headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=15)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"BingX API error on {path}: {data.get('msg')} (code {data.get('code')})")
    return data


def round_quantity(symbol: str, raw_quantity: float) -> float:
    precision = QUANTITY_PRECISION.get(symbol, 4)
    return round(raw_quantity, precision)


def set_leverage(symbol: str, position_side: str, leverage: int = DEFAULT_LEVERAGE) -> None:
    _signed_request("POST", "/openApi/swap/v2/trade/leverage",
                     {"symbol": symbol, "side": position_side, "leverage": str(leverage)})


def place_market_order(symbol: str, direction: str, quantity: float,
                        take_profit_price: float, stop_loss_price: float) -> dict:
    """direction: 'BUY' opens/adds to a LONG, 'SELL' opens/adds to a SHORT."""
    position_side = "LONG" if direction == "BUY" else "SHORT"
    set_leverage(symbol, position_side)

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
