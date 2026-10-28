from __future__ import annotations

import os

from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI

from .config import settings


def _timeout() -> int:
    """Секунды на один запрос к LLM. Шлюз AnyModel отвечает ~30 с (до 80 с), поэтому 120."""
    try:
        explicit = int(os.getenv("LLM_TIMEOUT", "").strip())
    except ValueError:
        explicit = 0
    if explicit > 0:
        return explicit
    return 120 if settings.LLM_PROVIDER == "anymodel" else 60


def _anymodel_options() -> dict[str, Any]:
    """AnyModel резервирует с баланса максимальную цену запроса, поэтому лимитируем ответ;
    «мышление» DeepSeek выключаем: оно дороже и медленнее, а JSON-ответу не нужно."""
    if settings.LLM_PROVIDER != "anymodel":
        return {}
    try:
        max_tokens = int(os.getenv("LLM_MAX_TOKENS", "").strip() or 4096)
    except ValueError:
        max_tokens = 4096
    return {"max_tokens": max_tokens, "extra_body": {"thinking": {"type": "disabled"}}}


@lru_cache(maxsize=8)
def get_chat_model_for(category: str = "tech", temperature: float = 0.7) -> ChatOpenAI:
    model_by_category = {
        "tech": settings.MODEL_TECH,
        "general": settings.MODEL_GENERAL,
    }
    model = model_by_category.get(category, settings.MODEL_TECH)
    headers = None
    if settings.LLM_PROVIDER == "openrouter":
        headers = {
            "HTTP-Referer": settings.OPENROUTER_REFERER or "https://sokrat.local",
            "X-Title": "Sokrat AI Interviewer",
        }
    return ChatOpenAI(
        model=model,
        api_key=settings.LLM_API_KEY or "missing",
        base_url=settings.LLM_BASE_URL,
        temperature=temperature,
        timeout=_timeout(),
        max_retries=2,
        default_headers=headers,
        **_anymodel_options(),
    )


def llm_configured() -> bool:
    """True, если для активного провайдера задан API-ключ."""
    return bool(settings.LLM_API_KEY)


def get_chat_model(temperature: float = 0.7) -> ChatOpenAI:
    return get_chat_model_for("tech", temperature)


def extract_text(content: Any) -> str:
    """Normalize LangChain message content to a plain string.

    Some providers (notably Anthropic via OpenRouter) return content as a
    list of blocks like [{"type": "text", "text": "..."}]; OpenAI-compatible
    endpoints return a plain string. This helper handles both shapes.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)
