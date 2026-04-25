from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI

from .config import settings


@lru_cache(maxsize=4)
def get_chat_model(temperature: float = 0.7) -> ChatOpenAI:
    headers = {
        "HTTP-Referer": settings.OPENROUTER_REFERER or "https://sokrat.local",
        "X-Title": "Sokrat AI Interviewer",
    }
    return ChatOpenAI(
        model=settings.MODEL_NAME,
        api_key=settings.OPENROUTER_API_KEY or "missing",
        base_url=settings.OPENROUTER_BASE_URL,
        temperature=temperature,
        timeout=60,
        max_retries=2,
        default_headers=headers,
    )


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
