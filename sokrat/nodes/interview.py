from __future__ import annotations

from typing import Any

from ..state import InterviewState


def interview_node(state: InterviewState) -> dict[str, Any]:
    """Pick the next question from the pre-built pool.

    The pool is constructed in app.py (bank + LLM-generated mix) before the
    interview starts; the node just advances the cursor and exposes
    `current_question` for the UI layer.
    """
    questions = state.get("questions") or []
    question_num = state.get("question_num") or 0
    errors = list(state.get("errors") or [])

    if question_num >= len(questions):
        errors.append("interview: pool exhausted")
        return {
            "current_step": "summary",
            "errors": errors,
        }

    q = questions[question_num]
    return {
        "current_question": q,
        "assistant_output": q.get("question", ""),
        "current_step": "in_interview",
        "errors": errors,
    }
