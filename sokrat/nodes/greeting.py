from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from ..state import InterviewState

GREETING_TEXT: str = (
    "Привет! Я — Sokrat, тренажёр для подготовки к собеседованиям. "
    "Я проведу с вами mock-интервью, дам мгновенный фидбэк после каждого ответа "
    "и в конце соберу отчёт с оценкой и рекомендациями.\n\n"
    "Чтобы начать, опишите одной фразой: на какую роль вы готовитесь, "
    "какой уровень (junior / middle / senior) и тип интервью "
    "(technical, hr или mixed). Пример: «middle backend technical»."
)


def greeting_node(state: InterviewState) -> dict[str, Any]:  # noqa: ARG001
    return {
        "session_id": uuid.uuid4().hex[:12],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "questions_asked": [],
        "answers": [],
        "questions_total": settings.INTERVIEW_QUESTIONS_COUNT,
        "errors": [],
        "current_step": "collect_context",
        "assistant_output": GREETING_TEXT,
    }
