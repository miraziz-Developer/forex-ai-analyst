import logging
import threading
import time
from datetime import datetime, timezone

import requests

import storage
from notifier import send_telegram_message

logger = logging.getLogger(__name__)

_HELP_TEXT = """📊 Kuzatuv buyruqlari
/status — bot va savdo rejimi holati
/stats — umumiy WIN/LOSS va P&L
/open — ochiq signallar
/recent — oxirgi 10 yopilgan signal
/help — buyruqlar ro'yxati"""


def format_stats(stats: dict) -> str:
    all_time, month = stats["all_time"], stats["last_30_days"]
    return (
        "📊 SAVDO STATISTIKASI\n\n"
        f"Ochiq signallar: {stats['open_signals']}\n\n"
        "Barcha vaqt:\n"
        f"✅ WIN: {all_time.get('WIN', 0)}\n"
        f"❌ LOSS: {all_time.get('LOSS', 0)}\n"
        f"⌛ EXPIRED: {all_time.get('EXPIRED', 0)}\n"
        f"🎯 Win rate: {all_time.get('win_rate_pct') or 0}%\n"
        f"💵 Ijro qilingan P&L: {all_time.get('realized_pnl_usdt', 0):+.2f} USDT\n\n"
        "Oxirgi 30 kun:\n"
        f"✅ {month.get('WIN', 0)}  ❌ {month.get('LOSS', 0)}  "
        f"🎯 {month.get('win_rate_pct') or 0}%  "
        f"💵 {month.get('realized_pnl_usdt', 0):+.2f} USDT"
    )


def _price(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.8f}".rstrip("0").rstrip(".")


def format_signals(rows: list[dict], title: str) -> str:
    if not rows:
        return f"{title}\n\nMa'lumot yo'q."
    lines = [title, ""]
    icons = {"OPEN": "🟡", "WIN": "✅", "LOSS": "❌", "EXPIRED": "⌛"}
    for row in rows:
        status = row.get("status") or row.get("outcome") or "OPEN"
        pnl = row.get("estimated_pnl_usdt")
        pnl_text = f" | P&L {pnl:+.2f} USDT" if pnl is not None else ""
        executed = "demo order" if row.get("executed") or row.get("broker_qty") else "signal"
        lines.extend([
            f"{icons.get(status, '•')} #{row['id']} {row['pair']} {row['direction']} — {status}",
            f"Kirish {_price(row['entry_price'])} | TP {_price(row['target_price'])} | SL {_price(row['stop_price'])}",
            f"{executed}{pnl_text} | {str(row['signal_time'])[:16].replace('T', ' ')} UTC",
            "",
        ])
    return "\n".join(lines).rstrip()


def command_response(command: str, auto_execute: bool, dashboard_url: str) -> str:
    command = command.split("@", 1)[0].lower()
    if command in {"/start", "/help"}:
        suffix = f"\n\n🌐 Dashboard: {dashboard_url}" if dashboard_url else ""
        return _HELP_TEXT + suffix
    if command == "/status":
        return (
            "🟢 Bot ishlayapti\n"
            f"Vaqt: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"Savdo rejimi: {'BingX DEMO ijro yoqilgan' if auto_execute else 'faqat tahlil'}"
            + (f"\nDashboard: {dashboard_url}" if dashboard_url else "")
        )
    if command == "/stats":
        return format_stats(storage.get_stats())
    if command == "/open":
        data = storage.get_signals(status="OPEN", limit=10)
        return format_signals(data["signals"], "🟡 OCHIQ SIGNALLAR")
    if command == "/recent":
        data = storage.get_signals(status="RESOLVED", limit=10)
        return format_signals(data["signals"], "🕘 OXIRGI NATIJALAR")
    return "Noma'lum buyruq. /help ni yuboring."


def start_command_listener(bot_token: str, allowed_chat_id: str,
                           auto_execute: bool = False, dashboard_url: str = "") -> threading.Thread:
    """Start Telegram getUpdates long polling in a daemon thread."""
    def poll() -> None:
        offset = None
        base_url = f"https://api.telegram.org/bot{bot_token}"
        try:
            requests.post(f"{base_url}/setMyCommands", json={"commands": [
                {"command": "status", "description": "Bot holati"},
                {"command": "stats", "description": "WIN/LOSS va P&L"},
                {"command": "open", "description": "Ochiq signallar"},
                {"command": "recent", "description": "Oxirgi natijalar"},
                {"command": "help", "description": "Buyruqlar"},
            ]}, timeout=10).raise_for_status()
        except requests.RequestException:
            logger.exception("Telegram command menu setup failed")

        while True:
            try:
                response = requests.get(f"{base_url}/getUpdates",
                                        params={"timeout": 25, "offset": offset}, timeout=35)
                response.raise_for_status()
                for update in response.json().get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    text = message.get("text", "").strip()
                    if chat_id != str(allowed_chat_id):
                        logger.warning("Ignored Telegram command from unauthorized chat %s", chat_id)
                        continue
                    if not text.startswith("/"):
                        continue
                    try:
                        reply = command_response(text.split()[0], auto_execute, dashboard_url)
                    except Exception:
                        logger.exception("Telegram command failed: %s", text)
                        reply = "⚠️ Ma'lumotni olishda xatolik yuz berdi. Keyinroq qayta urinib ko'ring."
                    send_telegram_message(reply, bot_token, allowed_chat_id)
            except requests.RequestException:
                logger.exception("Telegram command polling failed; retrying")
                time.sleep(5)

    thread = threading.Thread(target=poll, name="telegram-command-listener", daemon=True)
    thread.start()
    logger.info("Telegram command listener started")
    return thread