from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from ..llm import extract_text, get_chat_model
from ..prompts import load_prompt
from ..state import Answer, InterviewState
from ..storage import get_fallback_question
from ..utils.errors import format_error

_FALLBACK_OF_LAST_RESORT: str = (
    "Расскажите о самой сложной задаче, которую вы решали в своей работе."
)


def _format_history(questions: list[str], answers: list[Answer]) -> str:
    if not questions:
        return "(пока вопросов не было)"
    lines: list[str] = []
    for idx, question in enumerate(questions, start=1):
        if idx - 1 < len(answers):
            ans = answers[idx - 1]
            lines.append(f"{idx}. Q: {question} | reaction: score {ans['score']}/10")
        else:
            lines.append(f"{idx}. Q: {question} | reaction: (нет оценки)")
    return "\n".join(lines)


def interview_node(state: InterviewState) -> dict[str, Any]:
    role = state.get("role") or "backend"
    level = state.get("level") or "middle"
    interview_type = state.get("interview_type") or "technical"
    questions_asked = list(state.get("questions_asked", []))
    answers = list(state.get("answers", []))
    questions_total = state.get("questions_total", 0)
    question_index = len(questions_asked) + 1

    template = load_prompt("interviewer")
    prompt = template.format(
        role=role,
        level=level,
        interview_type=interview_type,
        question_index=question_index,
        questions_total=questions_total,
        history=_format_history(questions_asked, answers),
    )

    errors = list(state.get("errors", []))
    question_text: str = ""

    try:
        model = get_chat_model(temperature=0.7)
        response = model.invoke([HumanMessage(content=prompt)])
        question_text = extract_text(response.content).strip().strip('"').strip()
    except Exception as exc:  # noqa: BLE001
        errors.append(format_error("interview_llm_error", exc))

    if not question_text:
        fallback = get_fallback_question(
            role, level, interview_type, exclude=questions_asked
        )
        if fallback is None:
            fallback = _FALLBACK_OF_LAST_RESORT
            errors.append("interview_fallback_exhausted")
        question_text = fallback

    questions_asked.append(question_text)

    return {
        "questions_asked": questions_asked,
        "current_question": question_text,
        "assistant_output": question_text,
        "current_step": "analyze_answer",
        "errors": errors,
    }
