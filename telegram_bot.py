"""Authenticated Telegram webhook commands and PDF knowledge uploads."""
from __future__ import annotations

import os
import requests

import knowledge
from notifier import send_telegram_message


def _allowed(chat_id: str) -> bool:
    allowed = {item.strip() for item in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if item.strip()}
    return bool(allowed) and chat_id in allowed


def _reply(chat_id: str, text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if token:
        send_telegram_message(text, token, chat_id)


def _download(file_id: str) -> bytes:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    response = requests.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id}, timeout=20)
    response.raise_for_status()
    path = response.json()["result"]["file_path"]
    file_response = requests.get(f"https://api.telegram.org/file/bot{token}/{path}", timeout=60)
    file_response.raise_for_status()
    return file_response.content


def handle_update(update: dict) -> None:
    message = update.get("message") or update.get("channel_post") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if not chat_id or not _allowed(chat_id):
        return
    text = (message.get("text") or "").strip()
    if text.startswith("/start") or text.startswith("/help"):
        _reply(chat_id, "Buyruqlar: /status, /knowledge, /knowledge_search <savol>. PDF yuboring — bilim bazasiga qo‘shiladi.")
        return
    if text.startswith("/knowledge_search"):
        query = text.partition(" ")[2].strip()
        rows = knowledge.search(query) if query else []
        if not rows:
            _reply(chat_id, "Mos bilim topilmadi. Savolni aniqroq yozing.")
        else:
            _reply(chat_id, "\n\n".join(f"📄 {row['file_name']} — {row['page_number']}-sahifa\n{row['content'][:700]}" for row in rows))
        return
    if text.startswith("/knowledge"):
        rows = knowledge.documents()
        _reply(chat_id, "Bilim bazasi bo‘sh." if not rows else "\n".join(f"#{r['id']} {r['file_name']} ({r['page_count']} sahifa)" for r in rows))
        return
    document = message.get("document") or {}
    if document:
        name = document.get("file_name", "document.pdf")
        if not name.lower().endswith(".pdf"):
            _reply(chat_id, "Faqat PDF yuboring.")
            return
        try:
            result = knowledge.ingest_pdf(_download(document["file_id"]), name, document["file_id"], chat_id)
            state = "oldin yuklangan" if result["duplicate"] else f"saqlandi: {result['chunks']} bo‘lak"
            _reply(chat_id, f"PDF #{result['id']} {state} ({result['pages']} sahifa).")
        except Exception as exc:
            _reply(chat_id, f"PDF qabul qilinmadi: {type(exc).__name__}")