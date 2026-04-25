from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..state import InterviewState

GREETING_TEXT: str = (
    "Привет! Я — Sokrat, тренажёр для подготовки к собеседованиям. "
    "Я проведу с вами mock-интервью, дам мгновенный фидбэк после каждого ответа "
    "и в конце соберу отчёт с оценкой и рекомендациями."
)


def greeting_node(state: InterviewState) -> dict[str, Any]:
    """Initialize session metadata. Idempotent — won't overwrite an existing
    session_id so app.py can pre-set it before the first invocation."""
    patch: dict[str, Any] = {
        "assistant_output": GREETING_TEXT,
        "current_step": "collect_context",
    }
    if not state.get("session_id"):
        patch["session_id"] = uuid.uuid4().hex[:12]
    if not state.get("started_at"):
        patch["started_at"] = datetime.now(timezone.utc).isoformat()
    if state.get("answers") is None:
        patch["answers"] = []
    if state.get("scores") is None:
        patch["scores"] = []
    if state.get("errors") is None:
        patch["errors"] = []
    return patch
