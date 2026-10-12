"""Персоны интервьюера и стресс-режим: влияют на тон вопросов и обратной связи."""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PERSONA = "neutral"
STRESS_TIME_LIMIT_SEC = 90


@dataclass(frozen=True)
class Persona:
    key: str
    label: str
    hint: str
    question_style: str
    feedback_style: str


PERSONAS: dict[str, Persona] = {
    "neutral": Persona(
        key="neutral",
        label="🎭 Нейтральный",
        hint="сбалансированный интервьюер",
        question_style="Стандартный профессиональный тон, сбалансированная глубина.",
        feedback_style="доброжелательный, но честный",
    ),
    "bank": Persona(
        key="bank",
        label="🏦 Банк / корпорация",
        hint="строго, процессы, надёжность",
        question_style=(
            "Тон формальный и строгий. Упор на надёжность, безопасность, отказоустойчивость, "
            "соблюдение процессов и регламентов, работу с данными и транзакциями, "
            "ответственность за изменения в продакшене. Спрашивай про последствия ошибок."
        ),
        feedback_style="сдержанный, формальный, с акцентом на точность формулировок и риски",
    ),
    "startup": Persona(
        key="startup",
        label="🚀 Стартап",
        hint="скорость, ownership, неопределённость",
        question_style=(
            "Тон живой и неформальный. Упор на скорость поставки, ownership, работу в "
            "неопределённости, широту ролей, приоритизацию при нехватке ресурсов и "
            "прагматичные компромиссы вместо идеальной архитектуры."
        ),
        feedback_style="энергичный, прямой, ценит инициативу и прагматичность",
    ),
    "bigtech": Persona(
        key="bigtech",
        label="🏢 Big Tech",
        hint="масштаб, алгоритмы, system design",
        question_style=(
            "Тон профессиональный и въедливый. Упор на масштабируемость, сложность "
            "алгоритмов, system design, trade-offs, метрики и измеримый эффект. "
            "Задавай уточняющие вопросы про edge cases и нагрузку."
        ),
        feedback_style="аналитичный, требовательный к обоснованию решений и trade-offs",
    ),
}

STRESS_QUESTION_STYLE = (
    "СТРЕСС-ИНТЕРВЬЮ: вопросы с давлением — ставь кандидата в условия жёстких ограничений "
    "(«у тебя 2 минуты», «прод лежит, что делаешь первым?»), задавай каверзные и "
    "противоречивые уточнения, проверяй устойчивость позиции."
)
STRESS_FEEDBACK_STYLE = (
    "СТРЕСС-РЕЖИМ: говори коротко и жёстко, без смягчений; в feedback обязательно задай "
    "один каверзный уточняющий вопрос, как это сделал бы перебивающий интервьюер; "
    "отмечай воду и уклончивость."
)

_BANK_MARKERS = ("банк", "финанс", "страхов", "платёж", "платеж", "fintech", "финтех")
_STARTUP_MARKERS = ("стартап", "startup", "saas", "early-stage")
_BIGTECH_MARKERS = ("маркетплейс", "поиск", "облач", "экосистем", "big tech", "e-commerce")


def get_persona(key: str | None) -> Persona:
    return PERSONAS.get(key or "", PERSONAS[DEFAULT_PERSONA])


def suggest_persona(industry: str = "", company_description: str = "") -> str:
    """Подбирает персону по сфере компании; при сомнении — нейтральную."""
    text = f"{industry} {company_description}".lower()
    if any(m in text for m in _BANK_MARKERS):
        return "bank"
    if any(m in text for m in _STARTUP_MARKERS):
        return "startup"
    if any(m in text for m in _BIGTECH_MARKERS):
        return "bigtech"
    return DEFAULT_PERSONA


def question_style(persona_key: str | None, stress: bool) -> str:
    parts = [get_persona(persona_key).question_style]
    if stress:
        parts.append(STRESS_QUESTION_STYLE)
    return " ".join(parts)


def feedback_style(persona_key: str | None, stress: bool) -> str:
    style = f"тон обратной связи — {get_persona(persona_key).feedback_style}."
    if stress:
        style += " " + STRESS_FEEDBACK_STYLE
    return style
