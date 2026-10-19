"""Shared analyzer logic — used by app.py directly and by the LangGraph
analyze_answer node. Extracted so both call sites use one Pydantic schema
and one prompt path."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError, field_validator

from .key_points import (
    BONUS_PREFIX,
    apply_bonus_points,
    apply_fixed_points,
    format_fixed_points,
    validate_bonus_points,
    validate_key_points,
)
from .llm import get_chat_model_for
from .prompts import load_prompt

log = logging.getLogger(__name__)

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

# Судья должен быть максимально воспроизводимым: один и тот же ответ -> одна и та же оценка.
ANALYZER_TEMPERATURE = 0.0
_PROMPT_NAME = "analyzer"
_PROMPT_HASH_LEN = 12
# Имя тега блока ответа в analyzer.txt. Вырезаем само имя (а не только тег целиком):
# так не собрать ни закрывающий тег, ни вариант с атрибутами, вложением или «＜＞».
_ANSWER_TAG_NAME_RE = re.compile(r"candidate_answer", re.IGNORECASE)


class AnswerEvaluation(BaseModel):
    intent: str
    # Поля анализа из нового analyzer.txt. Типы нестрогие, чтобы кривой формат
    # служебного поля не валил всю оценку; храним их для отладки и eval.
    injection_suspected: Optional[bool] = None
    expected_points: list[Any] = Field(default_factory=list)
    points: list[Any] = Field(default_factory=list)
    errors: list[Any] = Field(default_factory=list)
    # Флаги для расчёта балла в коде (sokrat.scoring), п.2.4 analyzer.txt.
    off_topic: Optional[bool] = None
    bonus: Optional[bool] = None
    # Разметка не сопоставилась с фиксированными пунктами и после повтора:
    # балл по коду не считаем, берём балл модели (Engine._answer_score).
    points_mismatch: bool = False
    # Разметка бонусных пунктов (в D не входят, covered даёт +1; sokrat.scoring.has_bonus).
    # Пусто — бонус берётся из флага bonus модели.
    bonus_marks: list[Any] = Field(default_factory=list)

    @field_validator("expected_points", "points", "errors", "bonus_marks", mode="before")
    @classmethod
    def _null_to_empty(cls, value: Any) -> Any:
        # Модель иногда пишет null вместо []; из-за служебного поля не валим всю оценку.
        return [] if value is None else value

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


def prompt_hash() -> str:
    """Короткий sha256 текущего analyzer.txt — чтобы в сессии было видно, каким промптом оценено."""
    digest = hashlib.sha256(load_prompt(_PROMPT_NAME).encode("utf-8")).hexdigest()
    return digest[:_PROMPT_HASH_LEN]


def strip_answer_tags(answer: str) -> str:
    """Убирает имя тега, пока оно встречается: «candidate_candidate_answeranswer» тоже."""
    previous = None
    while previous != answer:
        previous, answer = answer, _ANSWER_TAG_NAME_RE.sub("", answer)
    return answer


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
    key_points: list[str] | None = None,
    fixed_points: list[str] | None = None,
    bonus_points: list[str] | None = None,
) -> AnswerEvaluation:
    """key_points — нестрогие подсказки темы; fixed_points — эталонные пункты,
    которые судья размечает ровно в заданном виде (analyzer.txt п.2.1);
    bonus_points — необязательные пункты B1..Bk, только вместе с fixed_points."""
    template = load_prompt(_PROMPT_NAME)
    hints_text = ", ".join(hints) if hints else "(подсказок нет)"
    skills = [s for s in (required_skills or []) if s]
    skills_text = ", ".join(skills) if skills else "(не указаны)"
    points = [str(p).strip() for p in (key_points or []) if str(p).strip()]
    key_points_text = "; ".join(points) if points else "(не заданы)"
    fixed = validate_key_points(fixed_points) if fixed_points else None
    if fixed_points and fixed is None:
        log.warning("analyzer: fixed_points не прошли валидацию (%d шт.), оцениваю без них", len(fixed_points))
    bonus = (validate_bonus_points(bonus_points, fixed) or None) if fixed and bonus_points else None
    if fixed and bonus_points and bonus is None:
        log.warning("analyzer: bonus_points не прошли валидацию (%d шт.), оцениваю без них", len(bonus_points))
    safe_answer = strip_answer_tags(answer)

    prompt = template.format(
        role=role,
        level=level,
        interview_type=interview_type,
        question=question,
        hints=hints_text,
        key_points=key_points_text,
        fixed_points=format_fixed_points(fixed),
        bonus_points=format_fixed_points(bonus, prefix=BONUS_PREFIX),
        answer=safe_answer,
        clarifications_used=clarifications_used,
        required_skills=skills_text,
        elapsed_seconds=format_elapsed(elapsed_seconds),  # новым промптом не используется, оставлено для совместимости
        persona_style=persona_style.strip() or "стандартный",
    )

    model = get_chat_model_for(role_category, temperature=ANALYZER_TEMPERATURE)
    structured = model.with_structured_output(AnswerEvaluation, method="json_mode")

    first: tuple[AnswerEvaluation, str | None] | None = None
    try:
        first = _resolve(coerce_evaluation(structured.invoke([HumanMessage(content=prompt)])), fixed, bonus)
        if first[1] is None:
            return first[0]
    except (ValidationError, ValueError):
        pass

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
    try:
        second = _resolve(coerce_evaluation(structured.invoke(retry_messages)), fixed, bonus)
    except (ValidationError, ValueError):
        if first is not None and first[1] == _BONUS_PROBLEM:
            return _bonus_fallback(first[0], len(bonus or []))
        raise
    if second[1] is None:
        return second[0]
    for candidate in (second, first):
        if candidate is not None and candidate[1] == _BONUS_PROBLEM:
            return _bonus_fallback(candidate[0], len(bonus or []))
    result = second[0]
    log.warning(
        "analyzer: разметка не сопоставилась с %d фиксированными пунктами после повтора "
        "(i=%s), беру балл модели",
        len(fixed or []),
        [p.get("i") if isinstance(p, dict) else type(p).__name__ for p in result.points],
    )
    return result.model_copy(update={"points_mismatch": True})


_POINTS_PROBLEM = "points"
_BONUS_PROBLEM = "bonus"


def _bonus_fallback(result: AnswerEvaluation, n_bonus: int) -> AnswerEvaluation:
    # Обязательные пункты сопоставились — D считаем; бонус берём из флага модели.
    log.warning("analyzer: разметка %d бонусных пунктов не сопоставилась, бонус — по флагу модели", n_bonus)
    return result


def _resolve(
    result: AnswerEvaluation, fixed: list[str] | None, bonus: list[str] | None
) -> tuple[AnswerEvaluation, str | None]:
    """Подставляет фиксированные и бонусные пункты в разбор.

    Вторым элементом — что не сопоставилось: None, "points" (обязательные) или "bonus".
    При "bonus" обязательные уже подставлены, а bonus_marks очищены.
    """
    if not fixed or result.intent != "answer":
        return result.model_copy(update={"bonus_marks": []}), None
    matched = apply_fixed_points(result.points, fixed)
    if matched is None:
        return result, _POINTS_PROBLEM
    update: dict[str, Any] = {"expected_points": list(fixed), "points": matched, "bonus_marks": []}
    if not bonus:
        return result.model_copy(update=update), None
    marks = apply_bonus_points(result.bonus_marks, bonus)
    if marks is None:
        return result.model_copy(update=update), _BONUS_PROBLEM
    return result.model_copy(update={**update, "bonus_marks": marks}), None
