from __future__ import annotations

from typing import Any

from ..state import InterviewState


def collect_context_node(state: InterviewState) -> dict[str, Any]:
    """Validate that role/level/interview_type are populated. App.py collects
    them via Chainlit buttons before invoking the graph; this node is the
    contractual checkpoint enforcing that the context is complete."""
    role = state.get("role")
    level = state.get("level")
    interview_type = state.get("interview_type")

    missing = [
        name
        for name, value in (
            ("role", role),
            ("level", level),
            ("interview_type", interview_type),
        )
        if not value
    ]
    errors = list(state.get("errors") or [])

    if missing:
        errors.append(f"collect_context: missing fields: {', '.join(missing)}")
        return {
            "current_step": "collect_context",
            "errors": errors,
            "assistant_output": (
                "Не хватает данных для старта интервью: " + ", ".join(missing)
            ),
        }

    return {
        "current_step": "interview",
        "errors": errors,
        "assistant_output": (
            f"Контекст готов: роль — {role}, уровень — {level}, "
            f"тип — {interview_type}."
        ),
    }
