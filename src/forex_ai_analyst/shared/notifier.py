import logging

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_message(text: str, bot_token: str, chat_id: str) -> bool:
    try:
        response = requests.post(
            TELEGRAM_API_URL.format(token=bot_token),
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        # Never log str(exc)/repr(exc) here: requests embeds the full request
        # URL (which contains the bot token) in its exception message.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.error("Failed to send Telegram notification (%s, status=%s)", type(exc).__name__, status)
        return False
