"""Authenticated Telegram button UI and PDF knowledge uploads."""
from __future__ import annotations

import os
import re
from datetime import timezone
from urllib.parse import urlparse

import requests

from forex_ai_analyst.knowledge import service as knowledge
from forex_ai_analyst.trading.infrastructure import signal_repository as scalping_storage
from forex_ai_analyst.operations import runtime_controls as runtime_controls
from forex_ai_analyst.shared.notifier import send_telegram_message

_AWAITING_KNOWLEDGE_SEARCH: set[str] = set()
_MENU = {"inline_keyboard": [
    [{"text": "📊 Umumiy holat", "callback_data": "status"}, {"text": "💰 Foyda / zarar", "callback_data": "performance"}],
    [{"text": "📌 Ochiq orderlar", "callback_data": "positions"}, {"text": "✅ Yopiq orderlar", "callback_data": "closed_orders"}],
    [{"text": "📈 So‘nggi signallar", "callback_data": "signals"}, {"text": "🔄 Panelni yangilash", "callback_data": "menu"}],
    [{"text": "📚 Bilim bazasi", "callback_data": "knowledge"}, {"text": "🔎 Bilimdan qidirish", "callback_data": "search"}],
    [{"text": "📤 PDF yuklash", "callback_data": "upload"}, {"text": "❓ Yordam", "callback_data": "help"}],
]}


def _control_request(text: str) -> dict | None:
    """Parse only small, explicit operational intents; never infer a risky value."""
    normalized = " ".join(text.upper().replace("_", "-").split())
    if normalized in {"STOP", "TO'XTAT", "TO‘XTAT", "KILL SWITCH", "KILL SWITCH YOQ"}:
        return {"kill_switch": True}
    if normalized in {"DAVOM ET", "START DEMO", "DEMO YOQ", "KILL SWITCH OCHIR"}:
        return {"kill_switch": False, "demo_execution": True}
    if normalized in {"DEMO OCHIR", "DEMO STOP"}:
        return {"demo_execution": False}
    match = re.fullmatch(r"(?:BLOCK|BLOK) ([A-Z0-9]+-USDT)", normalized)
    if match:
        # A delta, not a precomputed full list: two operators previewing
        # concurrent BLOCK/UNBLOCK commands from the same stale snapshot must
        # not be able to silently undo each other. runtime_controls.apply()
        # resolves this against the live blocked_pairs value at confirm time.
        return {"blocked_pairs": {"add": [match.group(1)]}}
    match = re.fullmatch(r"(?:UNBLOCK|BLOKDAN OCH) ([A-Z0-9]+-USDT)", normalized)
    if match:
        return {"blocked_pairs": {"remove": [match.group(1)]}}
    match = re.fullmatch(r"RISK PCT\s+(\d+(?:\.\d+)?)", normalized)
    if match and 0 < float(match.group(1)) <= 100:
        return {"risk_per_trade_pct": float(match.group(1))}
    match = re.fullmatch(r"DAILY LOSS PCT\s+(\d+(?:\.\d+)?)", normalized)
    if match and 0 < float(match.group(1)) <= 100:
        return {"max_daily_loss_pct": float(match.group(1))}
    match = re.fullmatch(r"MARGIN PCT\s+(\d+(?:\.\d+)?)", normalized)
    if match and 0 < float(match.group(1)) <= 100:
        return {"max_margin_utilization_pct": float(match.group(1))}
    return None


def _describe_control_value(key: str, value) -> str:
    if key == "blocked_pairs" and isinstance(value, dict):
        op, pairs = next(iter(value.items()))
        return f"{key} {op} {','.join(pairs)}"
    return f"{key}={value}"


def _preview_control(chat_id: str, updates: dict) -> str:
    code, expires = runtime_controls.create_pending(chat_id, updates)
    items = ", ".join(_describe_control_value(key, value) for key, value in updates.items())
    return (f"⚠️ Runtime o‘zgarishi preview: {items}\n"
            "Bu faqat keyingi VST orderlarga ta’sir qiladi; live trading yoqilmaydi.\n"
            f"Qo‘llash uchun 10 daqiqa ichida: TASDIQLAYMAN {code}\n"
            f"Muddati: {expires.strftime('%H:%M UTC')}")


def _target_label(row: dict) -> str:
    target = float(row.get("target_price") or 0)
    return f"{target:.6g}" if target > 0 else "kanal chiqishi"


def _allowed(chat_id: str) -> bool:
    allowed = {item.strip() for item in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if item.strip()}
    return bool(allowed) and chat_id in allowed


def configure_webhook(public_base_url: str | None = None) -> bool:
    """Register this service as Telegram's update receiver.

    Telegram notifications only require ``sendMessage``, whereas commands and
    inline buttons require an incoming webhook. Re-registering is safe and
    ensures callback queries remain enabled after a deploy.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    configured_url = public_base_url or os.environ.get("PUBLIC_BASE_URL", "")
    # Render provides the public onrender.com hostname at runtime. This keeps
    # button callbacks working even when PUBLIC_BASE_URL was not entered by hand.
    render_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    base_url = (configured_url or (f"https://{render_hostname}" if render_hostname else "")).strip().rstrip("/")
    parsed = urlparse(base_url)
    if not token or not secret or parsed.scheme != "https" or not parsed.netloc:
        return False

    api_url = f"https://api.telegram.org/bot{token}"
    try:
        response = requests.post(f"{api_url}/setWebhook", json={
            "url": f"{base_url}/telegram/webhook",
            "secret_token": secret,
            "allowed_updates": ["message", "channel_post", "callback_query"],
            "drop_pending_updates": False,
        }, timeout=15)
        response.raise_for_status()
        # The control panel is intentionally button-first. /start remains Telegram's
        # native entry point, but no command list is exposed to the user.
        requests.post(f"{api_url}/setMyCommands", json={"commands": []}, timeout=15).raise_for_status()
        return True
    except requests.RequestException:
        return False


def _reply(chat_id: str, text: str, *, menu: bool = False) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return
    # Telegram accepts at most 4,096 characters per message. Keep retrieval
    # responses readable instead of losing the entire response on a long PDF excerpt.
    chunks = [text[index:index + 4000] for index in range(0, len(text), 4000)] or [""]
    if not menu:
        for chunk in chunks:
            send_telegram_message(chunk, token, chat_id)
        return
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": chat_id, "text": chunks[0], "reply_markup": _MENU}, timeout=10)
        response.raise_for_status()
        for chunk in chunks[1:]:
            send_telegram_message(chunk, token, chat_id)
    except requests.RequestException:
        for chunk in chunks:
            send_telegram_message(chunk, token, chat_id)


def _answer_callback(callback_id: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if token and callback_id:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                          json={"callback_query_id": callback_id}, timeout=10).raise_for_status()
        except requests.RequestException:
            pass


def _download(file_id: str) -> bytes:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    response = requests.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id}, timeout=20)
    response.raise_for_status()
    path = response.json()["result"]["file_path"]
    file_response = requests.get(f"https://api.telegram.org/file/bot{token}/{path}", timeout=60)
    file_response.raise_for_status()
    return file_response.content


def _menu(chat_id: str) -> None:
    _reply(chat_id, "📈 Donchian 4H VST Trader boshqaruv paneli\n\nBarcha ma’lumotlarni quyidagi tugmalar orqali ko‘ring.", menu=True)


def _status_text() -> str:
    environment_kill_switch = os.environ.get("KILL_SWITCH", "false").strip().lower() == "true"
    try:
        controls = runtime_controls.settings()
        runtime_kill_switch = bool(controls["kill_switch"])
        runtime_demo_disabled = controls["demo_execution"] is False
    except Exception:
        runtime_kill_switch = True
        runtime_demo_disabled = True
    enabled = (os.environ.get("AUTO_EXECUTE_TRADES", "false").strip().lower() == "true" and
               not environment_kill_switch and not runtime_kill_switch and not runtime_demo_disabled and
               bool(os.environ.get("BINGX_API_KEY", "").strip()) and bool(os.environ.get("BINGX_SECRET", "").strip()))
    try:
        open_signals = scalping_storage.open_paper_positions()
        daily_trades, daily_pnl = scalping_storage.risk_state()
    except Exception:
        open_signals = "ma’lumot vaqtincha olinmadi"
        daily_trades, daily_pnl = "—", None
    daily_pnl_text = "—" if daily_pnl is None else f"{daily_pnl:+.4g} USDT"
    return ("📊 Tizim holati\n\n"
            f"BingX VST execution: {'🟢 faol' if enabled else '🔴 o‘chiq'}\n"
            f"Kill switch: {'🔴 yoqilgan' if environment_kill_switch or runtime_kill_switch else '🟢 o‘chiq'}\n"
            f"Ochiq order: {open_signals}\n"
            f"Bugungi trade: {daily_trades} | P&L: {daily_pnl_text}\n"
            f"Scan interval: {os.environ.get('MULTI_STRATEGY_SCAN_INTERVAL_SECONDS', '300')} soniya\n"
            "Strategiya: Donchian 4h long (qoidaga asoslangan, AI yo‘q); faqat BingX VST/demo.")


def _signals_text() -> str:
    try:
        rows = scalping_storage.recent_candidates(5)
    except Exception:
        return "📈 Signal journaliga vaqtincha ulanib bo‘lmadi. Keyinroq 🔄 Yangilash tugmasini bosing."
    if not rows:
        return "📈 Hali signal journalida yozuv yo‘q."
    return "\n".join(["📈 So‘nggi 5 signal:", *[
        f"• {row['pair']} {row['direction']} — {row['status']} | score: {row['score']}/100" for row in rows]])


def _knowledge_text() -> str:
    try:
        rows = knowledge.documents()
    except Exception:
        return "📚 Bilim bazasiga vaqtincha ulanib bo‘lmadi. Keyinroq qayta urinib ko‘ring."
    return "📚 Bilim bazasi bo‘sh." if not rows else "📚 Bilim bazasi:\n" + "\n".join(
        f"• #{row['id']} {row['file_name']} ({row['page_count']} sahifa)" for row in rows)


def _search_text(query: str) -> str:
    if len(query.strip()) < 3:
        return "Qidiruv uchun kamida 3 belgilik savol yoki kalit so‘z yuboring."
    try:
        rows = knowledge.search(query)
    except Exception:
        return "Bilim bazasida qidiruv vaqtincha ishlamadi. Keyinroq qayta urinib ko‘ring."
    if not rows:
        return "Mos bilim topilmadi. Boshqa aniqroq savol yuboring."
    return "\n\n".join(f"📄 {row['file_name']} — {row['page_number']}-sahifa\n{row['content'][:700]}" for row in rows)


def _positions_text() -> str:
    try:
        rows = scalping_storage.open_paper_signals()
    except Exception:
        return "📌 Ochiq pozitsiyalar vaqtincha olinmadi. Keyinroq 🔄 Yangilash tugmasini bosing."
    if not rows:
        return "📌 Hozir ochiq VST/paper pozitsiya yo‘q."
    lines = ["📌 Ochiq VST/paper orderlar:"]
    for row in rows[:10]:
        order = f" | order: #{row['broker_order_id']}" if row.get("broker_order_id") else " | paper signal"
        risk = f"${float(row['risk_usdt']):.4g}" if row.get("risk_usdt") is not None else "—"
        quantity = f"{float(row['quantity']):.8g}" if row.get("quantity") is not None else "—"
        lines.append(f"• {row['pair']} {row['direction']}{order}\n"
                     f"  Entry: {float(row['entry_price']):.6g} | TP: {_target_label(row)} | SL: {float(row['stop_price']):.6g}\n"
                     f"  Risk: {risk} | Quantity: {quantity}")
    return "\n".join(lines)


def _performance_text() -> str:
    try:
        summary = scalping_storage.performance_summary()
    except Exception:
        return "💰 Foyda/zarar ma’lumoti vaqtincha olinmadi. Keyinroq 🔄 Panelni yangilash tugmasini bosing."
    pnl = float(summary["realized_pnl_usdt"])
    win_rate = summary["win_rate_pct"]
    journal = ("💰 Jurnal foyda / zarar (candle-based hisob)\n\n"
            f"Jami yopilgan order: {summary['closed_orders']}\n"
            f"✅ WIN: {summary['wins']} | ❌ LOSS: {summary['losses']} | ⏱ TIME EXIT: {summary.get('time_exits', 0)}\n"
            f"🎯 Win rate: {win_rate:.1f}%" if win_rate is not None else "💰 Jurnal foyda / zarar (candle-based hisob)\n\n"
            f"Jami yopilgan order: {summary['closed_orders']}\n"
            f"✅ WIN: {summary['wins']} | ❌ LOSS: {summary['losses']} | ⏱ TIME EXIT: {summary.get('time_exits', 0)}\n"
            "🎯 Win rate: —") + f"\n💵 Journal P&L: {pnl:+.4g} USDT\nBugungi journal P&L: {float(summary['today_pnl_usdt']):+.4g} USDT"
    try:
        from forex_ai_analyst.trading.infrastructure import bingx_broker as broker
        started = scalping_storage.first_broker_order_time()
        if not started:
            return journal + "\n\n🔗 BingX VST: jurnalda exchange order hali yo‘q."
        if not (os.environ.get("BINGX_API_KEY", "").strip() and os.environ.get("BINGX_SECRET", "").strip()):
            return journal + "\n\n🔗 BingX VST actual P&L tekshiruvi uchun API credentials sozlanmagan."
        income = broker.vst_income_summary(int(started.astimezone(timezone.utc).timestamp() * 1000))
        return (journal + "\n\n🔗 BingX VST account income (API orqali tasdiqlangan)\n"
                f"Realized P&L: {income['realized_pnl_usdt']:+.4g} USDT\n"
                f"Komissiya: -{income['fees_usdt']:.4g} USDT | Funding: {income['funding_usdt']:+.4g} USDT\n"
                f"Actual net: {income['net_pnl_usdt']:+.4g} USDT\n"
                f"Income yozuvlari: {income['entries']}\n"
                "Eslatma: bu BingX VST account jami; manual/boshqa orderlar ham bo‘lsa kiradi. Journal P&L bilan aralashtirilmaydi.")
    except Exception as exc:
        return journal + f"\n\n🔗 BingX VST actual P&L hozir tekshirilmadi: {type(exc).__name__}."


def _closed_orders_text() -> str:
    try:
        rows = scalping_storage.closed_paper_signals(10)
    except Exception:
        return "✅ Yopiq orderlar vaqtincha olinmadi. Keyinroq 🔄 Panelni yangilash tugmasini bosing."
    if not rows:
        return "✅ Hali yopilgan VST/paper order yo‘q."
    lines = ["✅ So‘nggi 10 yopiq VST/paper order:"]
    for row in rows:
        pnl = float(row["realized_pnl_usdt"])
        order = f" | order: #{row['broker_order_id']}" if row.get("broker_order_id") else " | paper signal"
        lines.append(f"• {row['pair']} {row['direction']} — {row['status']}{order}\n"
                     f"  Entry: {float(row['entry_price']):.6g} | Exit: {float(row['outcome_price']):.6g} | P&L: {pnl:+.4g} USDT")
    return "\n".join(lines)


def _handle_callback(callback: dict) -> None:
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if not chat_id or not _allowed(chat_id):
        return
    _answer_callback(str(callback.get("id", "")))
    action = str(callback.get("data", ""))
    if action == "status":
        _reply(chat_id, _status_text(), menu=True)
    elif action == "performance":
        _reply(chat_id, _performance_text(), menu=True)
    elif action == "signals":
        _reply(chat_id, _signals_text(), menu=True)
    elif action == "positions":
        _reply(chat_id, _positions_text(), menu=True)
    elif action == "closed_orders":
        _reply(chat_id, _closed_orders_text(), menu=True)
    elif action == "menu":
        _menu(chat_id)
    elif action == "knowledge":
        _reply(chat_id, _knowledge_text(), menu=True)
    elif action == "search":
        _AWAITING_KNOWLEDGE_SEARCH.add(chat_id)
        _reply(chat_id, "🔎 Bilim bazasidan qidirish uchun savol yoki kalit so‘z yuboring.", menu=True)
    elif action == "upload":
        _reply(chat_id, "📤 Endi PDF faylni shu chatga yuboring. Text-based PDF avtomatik bilim bazasiga qo‘shiladi.", menu=True)
    elif action == "help":
        _reply(chat_id, "Holatni tugmalar orqali ko‘ring. Runtime control misollari: STOP, START DEMO, BLOCK BTC-USDT, UNBLOCK BTC-USDT, RISK PCT 1.5, DAILY LOSS PCT 5, MARGIN PCT 25. Har biri preview va TASDIQLAYMAN kodi talab qiladi. Live trading, kod, credential va broker endpointi o‘zgarmaydi.", menu=True)


def handle_update(update: dict) -> None:
    """Handle only authorized Telegram messages and inline button callbacks."""
    callback = update.get("callback_query")
    if callback:
        _handle_callback(callback)
        return
    message = update.get("message") or update.get("channel_post") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if not chat_id or not _allowed(chat_id):
        return
    text = (message.get("text") or "").strip()
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    if command in {"/start", "/help"}:
        _menu(chat_id)
        return
    if command == "/status":
        _reply(chat_id, _status_text(), menu=True)
        return
    if command == "/signals":
        _reply(chat_id, _signals_text(), menu=True)
        return
    if command in {"/positions", "/open"}:
        _reply(chat_id, _positions_text(), menu=True)
        return
    # Legacy commands remain compatible, but the primary UI is the button menu.
    if text.startswith("/knowledge_search"):
        _reply(chat_id, _search_text(text.partition(" ")[2].strip()), menu=True)
        return
    if text.startswith("/knowledge"):
        _reply(chat_id, _knowledge_text(), menu=True)
        return
    document = message.get("document") or {}
    if document:
        _AWAITING_KNOWLEDGE_SEARCH.discard(chat_id)
        name = document.get("file_name", "document.pdf")
        if not name.lower().endswith(".pdf"):
            _reply(chat_id, "Faqat PDF yuboring.", menu=True)
            return
        try:
            result = knowledge.ingest_pdf(_download(document["file_id"]), name, document["file_id"], chat_id)
            state = "oldin yuklangan" if result["duplicate"] else f"saqlandi: {result['chunks']} bo‘lak"
            _reply(chat_id, f"✅ PDF #{result['id']} {state} ({result['pages']} sahifa).", menu=True)
        except Exception as exc:
            _reply(chat_id, f"PDF qabul qilinmadi: {type(exc).__name__}", menu=True)
        return
    if chat_id in _AWAITING_KNOWLEDGE_SEARCH and text:
        _AWAITING_KNOWLEDGE_SEARCH.discard(chat_id)
        _reply(chat_id, _search_text(text), menu=True)
        return
    confirmation = re.fullmatch(r"TASDIQLAYMAN\s+([A-Fa-f0-9]{6})", text, flags=re.IGNORECASE)
    if confirmation:
        try:
            applied = runtime_controls.confirm(chat_id, confirmation.group(1))
        except Exception:
            applied = None
        if applied is None:
            _reply(chat_id, "❌ Tasdiqlash kodi noto‘g‘ri yoki muddati tugagan. Yangi preview yuboring.", menu=True)
        else:
            _reply(chat_id, f"✅ Runtime control qo‘llandi va auditga yozildi: {applied}", menu=True)
        return
    try:
        updates = _control_request(text)
    except Exception:
        updates = None
    if updates is not None:
        _reply(chat_id, _preview_control(chat_id, updates), menu=True)
        return
    _reply(chat_id, "Buyruq tanilmadi. Tugmalardan foydalaning yoki ❓ Yordam ni bosing.", menu=True)