"""Authenticated Telegram button UI and PDF knowledge uploads."""
from __future__ import annotations

import os

import requests

import knowledge
import scalping_storage
from notifier import send_telegram_message

_AWAITING_KNOWLEDGE_SEARCH: set[str] = set()
_MENU = {"inline_keyboard": [
    [{"text": "📊 Holat", "callback_data": "status"}, {"text": "📈 So‘nggi signallar", "callback_data": "signals"}],
    [{"text": "📌 Ochiq VST pozitsiyalar", "callback_data": "positions"}, {"text": "🔄 Yangilash", "callback_data": "menu"}],
    [{"text": "📚 Bilim bazasi", "callback_data": "knowledge"}, {"text": "🔎 Bilimdan qidirish", "callback_data": "search"}],
    [{"text": "📤 PDF yuklash", "callback_data": "upload"}, {"text": "❓ Yordam", "callback_data": "help"}],
]}


def _allowed(chat_id: str) -> bool:
    allowed = {item.strip() for item in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if item.strip()}
    return bool(allowed) and chat_id in allowed


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
    _reply(chat_id, "🤖 AI VST Trader boshqaruv paneli. Kerakli bo‘limni tanlang.", menu=True)


def _status_text() -> str:
    enabled = (os.environ.get("AUTO_EXECUTE_TRADES", "false").strip().lower() == "true" and
               os.environ.get("KILL_SWITCH", "false").strip().lower() != "true" and
               bool(os.environ.get("BINGX_API_KEY", "").strip()) and bool(os.environ.get("BINGX_SECRET", "").strip()))
    try:
        open_signals = scalping_storage.open_paper_positions()
    except Exception:
        open_signals = "ma’lumot vaqtincha olinmadi"
    return ("📊 Tizim holati\n\n"
            f"BingX VST execution: {'🟢 faol' if enabled else '🔴 o‘chiq'}\n"
            f"Kill switch: {'🔴 yoqilgan' if os.environ.get('KILL_SWITCH', 'false').lower() == 'true' else '🟢 o‘chiq'}\n"
            f"Ochiq signal: {open_signals}\n"
            f"Scan interval: {os.environ.get('MULTI_STRATEGY_SCAN_INTERVAL_SECONDS', '300')} soniya\n"
            "AI model qarori faqat BingX VST/demo uchun ishlatiladi.")


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
        return "📌 Hozir ochiq AI VST/paper pozitsiya yo‘q."
    lines = ["📌 Ochiq AI VST/paper pozitsiyalar:"]
    for row in rows[:10]:
        order = f" | order: #{row['broker_order_id']}" if row.get("broker_order_id") else " | paper signal"
        lines.append(f"• {row['pair']} {row['direction']}\n  Entry: {float(row['entry_price']):.6g} | TP: {float(row['target_price']):.6g} | SL: {float(row['stop_price']):.6g}{order}")
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
    elif action == "signals":
        _reply(chat_id, _signals_text(), menu=True)
    elif action == "positions":
        _reply(chat_id, _positions_text(), menu=True)
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
        _reply(chat_id, "Tugmalardan status, signal, ochiq pozitsiya va knowledge bazani ko‘ring. PDF yuklash uchun PDF yuboring. Bu bot trade ochish/yopish tugmalarini bermaydi: execution faqat serverdagi AI risk qoidalari va BingX VST orqali boshqariladi.", menu=True)


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
    if text.startswith("/start") or text.startswith("/help"):
        _menu(chat_id)
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
    _menu(chat_id)