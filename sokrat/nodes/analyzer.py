from __future__ import annotations

from typing import Any

from ..analyzer import analyze_answer_llm, filter_skills_touched
from ..state import InterviewState
from ..utils.errors import format_error


def analyze_answer_node(state: InterviewState) -> dict[str, Any]:
    """Run LLM analysis on the user's answer to the current question.

    Pure passthrough to `sokrat.analyzer.analyze_answer_llm`; the node's job
    is to plumb state into the call and write the structured result back.
    Intent dispatch (clarification / dont_know / skip / answer) stays in the
    UI layer because it is fundamentally a user-experience routing concern,
    not graph orchestration.
    """
    role = state.get("role") or ""
    level = state.get("level") or ""
    interview_type = state.get("interview_type") or ""
    role_category = state.get("role_category") or "tech"
    required_skills = list(state.get("required_skills") or [])
    clarifications_used = state.get("clarifications_used") or 0
    elapsed_seconds = state.get("elapsed_seconds") or 0.0
    user_input = state.get("user_input") or ""
    current_question = state.get("current_question") or {}
    errors = list(state.get("errors") or [])

    try:
        evaluation = analyze_answer_llm(
            role=role,
            level=level,
            interview_type=interview_type,
            question=current_question.get("question", ""),
            hints=current_question.get("hints", []) or [],
            answer=user_input,
            clarifications_used=clarifications_used,
            required_skills=required_skills,
            elapsed_seconds=elapsed_seconds,
            role_category=role_category,
            persona_style=state.get("persona_style") or "",
        )
        evaluation.skills_touched = filter_skills_touched(
            evaluation.skills_touched, required_skills
        )
        return {
            "last_analysis": evaluation.model_dump(),
            "current_step": "analyzed",
            "errors": errors,
        }
    except Exception as exc:  # noqa: BLE001
        errors.append(format_error("analyze_llm_error", exc))
        return {
            "last_analysis": None,
            "current_step": "analyze_error",
            "errors": errors,
        }
