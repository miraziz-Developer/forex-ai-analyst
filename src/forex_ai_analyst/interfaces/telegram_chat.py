"""Bounded explanatory chat; it has no write capability."""
from __future__ import annotations

import json

from forex_ai_analyst.shared import llm
from forex_ai_analyst.trading.infrastructure import signal_repository as scalping_storage
from forex_ai_analyst.operations import runtime_controls as runtime_controls
from forex_ai_analyst.trading.application.learning import summarize
from forex_ai_analyst.trading.infrastructure.market_intelligence import status as intelligence_status


def context() -> dict:
    recent = scalping_storage.recent_candidates(40)
    return {"runtime_controls": runtime_controls.settings(), "performance": scalping_storage.performance_summary(),
            "open_positions": scalping_storage.open_paper_signals()[:10], "recent_candidates": recent[:15],
            "learning": summarize(recent, "BTC-USDT"), "intelligence": intelligence_status(),
            "reconciliation": scalping_storage.reconciliation_status(),
            "safety": "BingX VST/demo only. This chat cannot place orders, change code, expose credentials, or enable live trading."}


_CHAT_SYSTEM = ("Siz Uzbek tilidagi read-only trading tizimi yordamchisisiz. Faqat berilgan JSON faktlariga "
                "tayangan holda qisqa tushuntiring. Hech qachon buyruq, runtime o'zgarish, order, credential, kod "
                "yoki live tradingni va'da qilmang. Moliyaviy maslahat bermang; noaniqlikni ayting.")


def answer(question: str) -> str:
    """Answer in Uzbek using a small, read-only, bounded system snapshot."""
    if not llm.any_configured():
        return "AI chat uchun Claude (Foundry) yoki OpenAI credentials sozlanmagan. Tugmalardan tizim holatini ko‘rishingiz mumkin."
    payload = json.dumps({"savol": question[:1000], "tizim": context()}, ensure_ascii=False, default=str)
    text, _ = llm.complete_text(_CHAT_SYSTEM, payload)
    if not text:
        return "AI chat vaqtincha javob bera olmadi."
    return text[:3500]