"""Bounded explanatory chat; it has no write capability."""
from __future__ import annotations

import json
import logging
import os

from forex_ai_analyst.trading.infrastructure import signal_repository as scalping_storage
from forex_ai_analyst.operations import runtime_controls as runtime_controls
from forex_ai_analyst.trading.application.ai_trader import _azure_openai_base_url
from forex_ai_analyst.trading.application.learning import summarize
from forex_ai_analyst.trading.infrastructure.market_intelligence import status as intelligence_status

logger = logging.getLogger(__name__)


def context() -> dict:
    recent = scalping_storage.recent_candidates(40)
    return {"runtime_controls": runtime_controls.settings(), "performance": scalping_storage.performance_summary(),
            "open_positions": scalping_storage.open_paper_signals()[:10], "recent_candidates": recent[:15],
            "learning": summarize(recent, "BTC-USDT"), "intelligence": intelligence_status(),
            "reconciliation": scalping_storage.reconciliation_status(),
            "safety": "BingX VST/demo only. This chat cannot place orders, change code, expose credentials, or enable live trading."}


def answer(question: str) -> str:
    """Answer in Uzbek using a small, read-only, bounded system snapshot."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    azure_api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    azure_deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
    if not api_key and not (azure_api_key and azure_endpoint and azure_deployment):
        return "AI chat uchun OpenAI yoki Azure OpenAI credentials sozlanmagan. Tugmalardan tizim holatini ko‘rishingiz mumkin."
    try:
        from openai import OpenAI
        if azure_api_key and azure_endpoint and azure_deployment:
            client = OpenAI(api_key=azure_api_key, base_url=_azure_openai_base_url(azure_endpoint))
            model = azure_deployment
        else:
            client = OpenAI(api_key=api_key)
            model = os.environ.get("AI_TRADER_MODEL", "gpt-4o-mini")
        response = client.chat.completions.create(
            # Do not send max_tokens: newer Azure deployments reject it in
            # favour of max_completion_tokens, while older deployments do not
            # all accept the replacement. The reply is bounded below instead.
            model=model, temperature=0.2,
            messages=[{"role": "system", "content": "Siz Uzbek tilidagi read-only trading tizimi yordamchisisiz. Faqat berilgan JSON faktlariga tayangan holda qisqa tushuntiring. Hech qachon buyruq, runtime o'zgarish, order, credential, kod yoki live tradingni va'da qilmang. Moliyaviy maslahat bermang; noaniqlikni ayting."},
                      {"role": "user", "content": json.dumps({"savol": question[:1000], "tizim": context()}, ensure_ascii=False, default=str)}],
        )
        return (response.choices[0].message.content or "Javob olinmadi.")[:3500]
    except Exception as exc:
        logger.warning("Telegram AI chat request failed: %s", exc)
        return f"AI chat vaqtincha javob bera olmadi: {type(exc).__name__}."