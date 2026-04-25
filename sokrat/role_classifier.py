from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage

from .llm import extract_text, get_chat_model_for

log = logging.getLogger(__name__)

_TECH_KEYWORDS: tuple[str, ...] = (
    "developer",
    "engineer",
    "programmer",
    "devops",
    "sre",
    "qa",
    "tester",
    "data ",
    "ml",
    "ai ",
    "frontend",
    "backend",
    "fullstack",
    "full-stack",
    "analyst",
    "architect",
    "scientist",
    "разработчик",
    "программист",
    "инженер",
    "тестировщик",
    "аналитик",
    "админ",
    "архитектор",
    "девопс",
    "датасайентист",
    "data scientist",
)

_SYSTEM_PROMPT = (
    "Классифицируй должность строго на одну из двух категорий:\n"
    "- `tech` — IT, разработка, инженерия, аналитика данных, DevOps, QA, ML, "
    "любая роль, требующая программирования или работы со сложными техническими системами.\n"
    "- `general` — всё остальное: рабочие специальности, сервис, образование, "
    "торговля, физический труд, медицина, логистика и т.п.\n"
    "Ответь ровно одним словом: `tech` или `general`. Никаких пояснений."
)


def _matches_tech_keyword(role: str) -> bool:
    haystack = role.lower()
    return any(kw in haystack for kw in _TECH_KEYWORDS)


@lru_cache(maxsize=128)
def classify_role(role: str) -> str:
    normalized = (role or "").strip().lower()
    if not normalized:
        return "general"
    if _matches_tech_keyword(normalized):
        return "tech"
    try:
        model = get_chat_model_for("general", temperature=0.0)
        response = model.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=f"Должность: {role}"),
            ]
        )
        raw = extract_text(response.content).strip().lower()
    except Exception:
        log.exception("role classifier: LLM call failed for role=%r", role)
        return "general"
    return "tech" if raw.startswith("tech") else "general"
