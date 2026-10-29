"""Проверка связи с LLM-провайдером: python -m sokrat.llm_check"""
from __future__ import annotations

import json
import sys

from langchain_core.messages import HumanMessage

from .config import settings
from .llm import extract_text, get_chat_model_for, llm_configured


def main() -> int:
    print(f"Провайдер: {settings.LLM_PROVIDER} · {settings.LLM_BASE_URL}")
    print(f"Модели: tech={settings.MODEL_TECH} · general={settings.MODEL_GENERAL}")
    if not llm_configured():
        print("✗ API-ключ не задан (ANYMODEL_API_KEY / DEEPSEEK_API_KEY / OPENROUTER_API_KEY в .env)")
        return 1

    ok = True
    for category in ("tech", "general"):
        try:
            model = get_chat_model_for(category, temperature=0.0)
            reply = extract_text(model.invoke([HumanMessage(content="Ответь одним словом: ок")]).content)
            print(f"✓ {category}: обычный запрос → {reply.strip()[:40]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"✗ {category}: обычный запрос не прошёл: {exc}")
            ok = False
            continue
        try:
            model = get_chat_model_for(category, temperature=0.0).bind(
                response_format={"type": "json_object"}
            )
            raw = extract_text(
                model.invoke([HumanMessage(content='Верни JSON вида {"status": "ok"}')]).content
            )
            json.loads(raw)
            print(f"✓ {category}: JSON-режим работает")
        except Exception as exc:  # noqa: BLE001
            print(f"✗ {category}: JSON-режим не прошёл: {exc}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
