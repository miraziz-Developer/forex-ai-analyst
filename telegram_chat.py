"""Bounded explanatory chat; it has no write capability."""
from __future__ import annotations

import json
import os

import scalping_storage
import runtime_controls
from ai_trader import _azure_openai_base_url
from learning import summarize
from market_intelligence import status as intelligence_status


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
            model=model, temperature=0.2, max_tokens=500,
            messages=[{"role": "system", "content": "Siz Uzbek tilidagi read-only trading tizimi yordamchisisiz. Faqat berilgan JSON faktlariga tayangan holda qisqa tushuntiring. Hech qachon buyruq, runtime o'zgarish, order, credential, kod yoki live tradingni va'da qilmang. Moliyaviy maslahat bermang; noaniqlikni ayting."},
                      {"role": "user", "content": json.dumps({"savol": question[:1000], "tizim": context()}, ensure_ascii=False, default=str)}],
        )
        return (response.choices[0].message.content or "Javob olinmadi.")[:3500]
    except Exception as exc:
        return f"AI chat vaqtincha javob bera olmadi: {type(exc).__name__}."