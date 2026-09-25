"""Model access for the trading brain and the Telegram chat.

Provider order: Claude on Azure AI Foundry (AZURE_ANTHROPIC_*) first, then
OpenAI / Azure OpenAI as a fallback. Every call fails closed: an unavailable
provider, a refusal, truncation or invalid JSON returns None, and callers
treat None as "no trade". Errors are logged by type only, never with keys.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_CLAUDE_DEPLOYMENT = "claude-opus-5"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def claude_configured() -> bool:
    return bool(_env("AZURE_ANTHROPIC_ENDPOINT") and _env("AZURE_ANTHROPIC_API_KEY"))


def openai_configured() -> bool:
    return bool(_env("OPENAI_API_KEY")) or all(
        _env(k) for k in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"))


def any_configured() -> bool:
    return claude_configured() or openai_configured()


def _azure_openai_base_url(endpoint: str) -> str:
    """Return an OpenAI-compatible Azure endpoint without duplicating ``/openai/v1``."""
    normalized = endpoint.rstrip("/")
    if normalized.endswith("/openai/v1"):
        return f"{normalized}/"
    return f"{normalized}/openai/v1/"


# ---------- Claude on Azure AI Foundry ----------

def _claude_client():
    from anthropic import AnthropicFoundry
    # A decision runs inside the 5-minute scheduler tick: bound the worst case.
    return AnthropicFoundry(api_key=_env("AZURE_ANTHROPIC_API_KEY"),
                            base_url=_env("AZURE_ANTHROPIC_ENDPOINT").rstrip("/"),
                            timeout=150.0, max_retries=1)


def _claude_call(system: str, user: str, max_tokens: int, output_format: dict | None) -> str | None:
    import anthropic

    output_config = {"effort": _env("AZURE_ANTHROPIC_EFFORT") or "high"}
    if output_format:
        output_config["format"] = output_format
    try:
        response = _claude_client().messages.create(
            model=_env("AZURE_ANTHROPIC_DEPLOYMENT") or DEFAULT_CLAUDE_DEPLOYMENT,
            max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
    except anthropic.AuthenticationError:
        logger.error("Claude (Foundry) rejected the API key")
        return None
    except anthropic.RateLimitError:
        logger.warning("Claude (Foundry) rate limited")
        return None
    except anthropic.APIStatusError as exc:
        logger.warning("Claude (Foundry) API error: HTTP %s", exc.status_code)
        return None
    except anthropic.APIConnectionError as exc:
        logger.warning("Claude (Foundry) unreachable: %s", type(exc).__name__)
        return None
    if response.stop_reason != "end_turn":
        # refusal or max_tokens: never act on a partial or declined answer
        logger.warning("Claude (Foundry) stopped with %s", response.stop_reason)
        return None
    return next((b.text for b in response.content if b.type == "text"), None)


# ---------- OpenAI / Azure OpenAI ----------

def _openai_client_and_model():
    from openai import OpenAI
    if all(_env(k) for k in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")):
        # Azure AI Foundry / Azure OpenAI expose an OpenAI-compatible /openai/v1 endpoint.
        client = OpenAI(api_key=_env("AZURE_OPENAI_API_KEY"),
                        base_url=_azure_openai_base_url(_env("AZURE_OPENAI_ENDPOINT")))
        return client, _env("AZURE_OPENAI_DEPLOYMENT")
    return OpenAI(api_key=_env("OPENAI_API_KEY")), os.environ.get("AI_TRADER_MODEL", "gpt-4o-mini")


def _openai_call(system: str, user: str, schema: dict | None) -> str | None:
    """Responses API: works for current Foundry/OpenAI deployments, including
    reasoning models that reject `temperature` and Chat Completions."""
    try:
        client, model = _openai_client_and_model()
        kwargs = {}
        if schema is not None:
            kwargs["text"] = {"format": {"type": "json_schema", "name": "decision", "schema": schema, "strict": True}}
        response = client.responses.create(model=model, instructions=system, input=user, **kwargs)
        return response.output_text
    except Exception as exc:  # the openai package raises many error types; fail closed
        logger.warning("OpenAI provider failed: %s", type(exc).__name__)
        return None


# ---------- public API ----------

def complete_json(system: str, user: str, schema: dict) -> tuple[dict | None, str]:
    """Structured decision JSON and the provider that produced it ("none" if all failed)."""
    if claude_configured():
        parsed = _loads(_claude_call(system, user, 16000, {"type": "json_schema", "schema": schema}))
        if parsed is not None:
            return parsed, "claude"
    if openai_configured():
        parsed = _loads(_openai_call(system, user, schema))
        if parsed is not None:
            return parsed, "openai"
    return None, "none"


def complete_text(system: str, user: str) -> tuple[str | None, str]:
    if claude_configured():
        text = _claude_call(system, user, 4000, None)
        if text:
            return text, "claude"
    if openai_configured():
        text = _openai_call(system, user, None)
        if text:
            return text, "openai"
    return None, "none"


def _loads(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
