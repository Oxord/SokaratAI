from __future__ import annotations

from typing import Any

from ..state import InterviewState
from ..utils.validators import (
    INTERVIEW_TYPES,
    LEVELS,
    ROLES,
    parse_context,
)


def _missing_fields_message(missing: list[str]) -> str:
    labels = {
        "role": f"роль (доступно: {', '.join(ROLES)})",
        "level": f"уровень ({', '.join(LEVELS)})",
        "interview_type": f"тип интервью ({', '.join(INTERVIEW_TYPES)})",
    }
    parts = [labels[m] for m in missing]
    example = "junior frontend technical"
    return (
        "Не удалось распознать "
        + " и ".join(parts)
        + f". Пожалуйста, укажите всё одной фразой, например: «{example}»."
    )


def collect_context_node(state: InterviewState) -> dict[str, Any]:
    raw = state.get("user_input", "") or ""

    role = state.get("role")
    level = state.get("level")
    interview_type = state.get("interview_type")

    parsed = parse_context(raw)
    role = role or parsed["role"]
    level = level or parsed["level"]
    interview_type = interview_type or parsed["interview_type"]

    missing = [
        name
        for name, value in (
            ("role", role),
            ("level", level),
            ("interview_type", interview_type),
        )
        if not value
    ]

    if missing:
        return {
            "role": role,
            "level": level,
            "interview_type": interview_type,
            "current_step": "collect_context",
            "assistant_output": _missing_fields_message(missing),
        }

    confirmation = (
        f"Принято: роль — {role}, уровень — {level}, тип интервью — {interview_type}. "
        f"Начинаем интервью из {state.get('questions_total', 0)} вопросов."
    )
    return {
        "role": role,
        "level": level,
        "interview_type": interview_type,
        "current_step": "interview",
        "assistant_output": confirmation,
    }
