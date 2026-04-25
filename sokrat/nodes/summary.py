from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage

from ..llm import extract_text, get_chat_model
from ..prompts import load_prompt
from ..state import Answer, InterviewState
from ..storage import save_session
from ..utils.errors import format_error


def _format_transcript(answers: list[Answer]) -> str:
    if not answers:
        return "(нет ответов)"
    blocks: list[str] = []
    for idx, answer in enumerate(answers, start=1):
        strengths = "; ".join(answer.get("strengths") or []) or "—"
        weaknesses = "; ".join(answer.get("weaknesses") or []) or "—"
        category = answer.get("category") or "other"
        blocks.append(
            f"[Q{idx}] {answer['question']}\n"
            f"[A{idx}] {answer['answer']}\n"
            f"   score: {answer['score']}/10 | category: {category}\n"
            f"   strengths: {strengths}\n"
            f"   weaknesses: {weaknesses}\n"
            f"   feedback: {answer.get('feedback', '')}"
        )
    return "\n\n".join(blocks)


def _compute_overall_score(answers: list[Answer]) -> float:
    if not answers:
        return 0.0
    total = sum(int(a.get("score", 0)) for a in answers)
    return round(total / len(answers), 1)


def summary_node(state: InterviewState) -> dict[str, Any]:
    role = state.get("role") or "—"
    level = state.get("level") or "—"
    interview_type = state.get("interview_type") or "—"
    answers = list(state.get("answers", []))
    questions_total = state.get("questions_total", len(answers))
    overall_score = _compute_overall_score(answers)
    errors = list(state.get("errors", []))

    template = load_prompt("summary")
    prompt = template.format(
        role=role,
        level=level,
        interview_type=interview_type,
        questions_total=questions_total,
        overall_score=overall_score,
        transcript=_format_transcript(answers),
    )

    summary_text: str
    try:
        response = get_chat_model(temperature=0.5).invoke(
            [HumanMessage(content=prompt)]
        )
        summary_text = extract_text(response.content).strip()
    except Exception as exc:  # noqa: BLE001
        errors.append(format_error("summary_llm_error", exc))
        summary_text = (
            f"## Общая оценка\nСредний балл: {overall_score}/10. "
            f"Автоматический отчёт недоступен из-за ошибки LLM."
        )

    finished_at = datetime.now(timezone.utc).isoformat()

    patch: dict[str, Any] = {
        "answers": answers,
        "summary": summary_text,
        "overall_score": overall_score,
        "finished_at": finished_at,
        "current_step": "done",
        "assistant_output": summary_text,
        "errors": errors,
    }

    try:
        save_session({**state, **patch})  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        errors.append(format_error("save_session_error", exc))

    patch["errors"] = errors
    return patch
