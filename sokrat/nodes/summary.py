from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..state import InterviewState
from ..summary_builder import compute_summary
from ..utils.errors import format_error


def summary_node(state: InterviewState) -> dict[str, Any]:
    """Aggregate per-question scores into the final report dict."""
    role = state.get("role") or ""
    level = state.get("level") or ""
    interview_type = state.get("interview_type") or ""
    questions = list(state.get("questions") or [])
    answers = list(state.get("answers") or [])
    scores = list(state.get("scores") or [])
    required_skills = list(state.get("required_skills") or [])
    errors = list(state.get("errors") or [])

    try:
        summary = compute_summary(
            role=role,
            level=level,
            interview_type=interview_type,
            questions=questions,
            answers=answers,
            scores=scores,
            required_skills=required_skills,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(format_error("summary_error", exc))
        summary = None

    return {
        "summary": summary,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "current_step": "save_session",
        "errors": errors,
    }
