"""Shared analyzer logic — used by app.py directly and by the LangGraph
analyze_answer node. Extracted so both call sites use one Pydantic schema
and one prompt path."""
from __future__ import annotations

from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from .llm import get_chat_model_for
from .prompts import load_prompt

ALLOWED_INTENTS: tuple[str, ...] = (
    "answer", "clarification", "dont_know", "skip", "meta",
)
ALLOWED_CATEGORIES: tuple[str, ...] = (
    "fundamentals", "system_design", "coding", "debugging",
    "soft_skills", "motivation", "behavioral", "other",
)

DIMENSION_LABELS: dict[str, str] = {
    "role_fit": "Соответствие должности",
    "structure": "Структура ответа",
    "literacy": "Грамотность",
    "oratory": "Ораторское мастерство",
    "depth": "Глубина знаний",
    "pace": "Темп",
}


class AnswerEvaluation(BaseModel):
    intent: str
    score: Optional[int] = Field(default=None, ge=0, le=10)
    feedback: str
    explanation: Optional[str] = None
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    category: str = "other"
    skills_touched: list[str] = Field(default_factory=list)
    role_fit: Optional[int] = Field(default=None, ge=0, le=10)
    structure: Optional[int] = Field(default=None, ge=0, le=10)
    literacy: Optional[int] = Field(default=None, ge=0, le=10)
    oratory: Optional[int] = Field(default=None, ge=0, le=10)
    depth: Optional[int] = Field(default=None, ge=0, le=10)
    ideal_answer: Optional[str] = None


def format_elapsed(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    if total < 60:
        return f"{total} сек"
    minutes, sec = divmod(total, 60)
    if sec == 0:
        return f"{minutes} мин"
    return f"{minutes} мин {sec} сек"


def coerce_evaluation(value) -> AnswerEvaluation:
    if isinstance(value, AnswerEvaluation):
        result = value
    elif isinstance(value, dict):
        result = AnswerEvaluation.model_validate(value)
    else:
        raise ValueError(f"Unexpected analyzer output type: {type(value).__name__}")

    if result.intent not in ALLOWED_INTENTS:
        result.intent = "answer"
    if result.category not in ALLOWED_CATEGORIES:
        result.category = "other"
    return result


def filter_skills_touched(touched: list[str], canonical: list[str]) -> list[str]:
    if not touched or not canonical:
        return []
    canon_lower = {s.lower(): s for s in canonical}
    out: list[str] = []
    seen: set[str] = set()
    for raw in touched:
        key = str(raw).strip().lower()
        if key in canon_lower and canon_lower[key] not in seen:
            out.append(canon_lower[key])
            seen.add(canon_lower[key])
    return out


def analyze_answer_llm(
    role: str,
    level: str,
    interview_type: str,
    question: str,
    hints: list[str],
    answer: str,
    clarifications_used: int,
    required_skills: list[str] | None = None,
    elapsed_seconds: float = 0.0,
    role_category: str = "tech",
    persona_style: str = "",
) -> AnswerEvaluation:
    template = load_prompt("analyzer")
    hints_text = ", ".join(hints) if hints else "(подсказок нет)"
    skills = [s for s in (required_skills or []) if s]
    skills_text = ", ".join(skills) if skills else "(не указаны)"
    prompt = template.format(
        role=role,
        level=level,
        interview_type=interview_type,
        question=question,
        hints=hints_text,
        answer=answer,
        clarifications_used=clarifications_used,
        required_skills=skills_text,
        elapsed_seconds=format_elapsed(elapsed_seconds),
        persona_style=persona_style.strip() or "стандартный",
    )

    model = get_chat_model_for(role_category, temperature=0.3)
    structured = model.with_structured_output(AnswerEvaluation, method="json_mode")

    try:
        return coerce_evaluation(structured.invoke([HumanMessage(content=prompt)]))
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
        return coerce_evaluation(structured.invoke(retry_messages))
