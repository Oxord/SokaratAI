"""Единый источник порогов для оценок: по вопросу, списки итогов, вердикт, индекс.

Балл за вопрос 0–10: 0 — пропуск / «не знаю», 1–5 — «Можно лучше»,
6–7 — «Хорошо», 8–10 — «Отлично».
"""
from __future__ import annotations

import math

GOOD_MIN = 6
EXCELLENT_MIN = 8

# Ранняя остановка без навыков: серия ответов не выше этого балла — сигнал «слабо».
STABLE_WEAK_MAX = 3

# Вердикт по итоговому проценту (percentage / 10) — те же полосы, что и для вопроса.
VERDICT_STRONG_MIN = EXCELLENT_MIN
VERDICT_COMPETENT_MIN = GOOD_MIN

# Ниже этой доли отвеченных вопросов вердикт не выдаётся.
MIN_ANSWERED_SHARE = 0.70

# Индекс готовности: сколько оценённых ответов нужно для точного числа.
READINESS_MIN_ANSWERS = 8
# Шаг округления индекса: грубый — при малом числе ответов, обычный — при достаточном.
READINESS_ROUND_STEP_ROUGH = 10
READINESS_ROUND_STEP = 5
# Шаг округления границ диапазона для неполного интервью.
READINESS_RANGE_STEP = 5

# Версия формулы итогов: динамика сравнивает только сессии одной версии.
SUMMARY_VERSION = 2

SKIPPED = "skipped"
NEEDS_WORK = "needs_work"
GOOD = "good"
EXCELLENT = "excellent"


def band(score: int | float) -> str:
    """Полоса для балла за вопрос."""
    if score <= 0:
        return SKIPPED
    if score >= EXCELLENT_MIN:
        return EXCELLENT
    if score >= GOOD_MIN:
        return GOOD
    return NEEDS_WORK


def is_weak(score: int | float) -> bool:
    """Отвеченный, но слабый ответ (1–5): тема для материалов, показ эталона."""
    return 0 < score < GOOD_MIN


def round_to(value: float, step: int) -> int:
    """Округление к ближайшему кратному step, половина — вверх (62.5 → 65 при step=5)."""
    return int(step * math.floor(value / step + 0.5))


def floor_to(value: float, step: int) -> int:
    return int(step * math.floor(value / step))


def ceil_to(value: float, step: int) -> int:
    return int(step * math.ceil(value / step))
