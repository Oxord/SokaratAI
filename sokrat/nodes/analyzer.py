from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from ..llm import get_chat_model
from ..prompts import load_prompt
from ..state import Answer, InterviewState
from ..utils.errors import format_error

ALLOWED_CATEGORIES: tuple[str, ...] = (
    "fundamentals",
    "system_design",
    "coding",
    "debugging",
    "soft_skills",
    "motivation",
    "behavioral",
    "other",
)


class AnswerEvaluation(BaseModel):
    score: int = Field(ge=1, le=10)
    strengths: list[str]
    weaknesses: list[str]
    feedback: str
    category: str


def _evaluate(role: str, level: str, interview_type: str,
              question: str, answer: str) -> AnswerEvaluation:
    template = load_prompt("analyzer")
    prompt = template.format(
        role=role,
        level=level,
        interview_type=interview_type,
        question=question,
        answer=answer,
    )

    model = get_chat_model(temperature=0.2)
    structured = model.with_structured_output(AnswerEvaluation, method="json_mode")

    try:
        result = structured.invoke([HumanMessage(content=prompt)])
        return _coerce(result)
    except (ValidationError, ValueError):
        retry_messages = [
            SystemMessage(
                content=(
                    "ВЕРНИ СТРОГО валидный JSON-объект, "
                    "полностью соответствующий схеме, "
                    "без markdown, без префиксов, без пояснений."
                )
            ),
            HumanMessage(content=prompt),
        ]
        result = structured.invoke(retry_messages)
        return _coerce(result)


def _coerce(value: Any) -> AnswerEvaluation:
    if isinstance(value, AnswerEvaluation):
        return value
    if isinstance(value, dict):
        return AnswerEvaluation.model_validate(value)
    raise ValueError(f"Unexpected analyzer output type: {type(value).__name__}")


def analyze_answer_node(state: InterviewState) -> dict[str, Any]:
    role = state.get("role") or "backend"
    level = state.get("level") or "middle"
    interview_type = state.get("interview_type") or "technical"
    question = state.get("current_question") or ""
    answer_text = state.get("user_input") or ""
    answers = list(state.get("answers", []))
    questions_total = state.get("questions_total", 0)
    errors = list(state.get("errors", []))

    try:
        evaluation = _evaluate(role, level, interview_type, question, answer_text)
        category = (
            evaluation.category
            if evaluation.category in ALLOWED_CATEGORIES
            else "other"
        )
        new_answer: Answer = {
            "question": question,
            "answer": answer_text,
            "score": evaluation.score,
            "strengths": evaluation.strengths,
            "weaknesses": evaluation.weaknesses,
            "feedback": evaluation.feedback,
            "category": category,
        }
    except Exception as exc:  # noqa: BLE001
        errors.append(format_error("analyze_llm_error", exc))
        new_answer = {
            "question": question,
            "answer": answer_text,
            "score": 0,
            "strengths": [],
            "weaknesses": [],
            "feedback": "Не удалось проанализировать ответ — давай попробуем ещё раз.",
            "category": "error",
        }

    answers.append(new_answer)

    if len(answers) >= questions_total:
        next_step: str = "summary"
        assistant_output = (
            "Спасибо! Это был последний вопрос — формирую итоговый отчёт."
        )
    else:
        next_step = "interview"
        assistant_output = "Спасибо, переходим к следующему вопросу."

    return {
        "answers": answers,
        "current_step": next_step,
        "assistant_output": assistant_output,
        "errors": errors,
    }
