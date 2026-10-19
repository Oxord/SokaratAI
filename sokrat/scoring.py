"""Итоговый балл ответа по процедуре analyzer.txt п.2.4.

Модель отдаёт разбор (points, errors) и два флага (off_topic, bonus), а балл
считаем здесь: так одинаковый разбор всегда даёт одинаковую оценку.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any

POINT_STATUSES: frozenset[str] = frozenset({"covered", "partial", "missing"})

MIN_SCORE = 1
MIN_SCORED_ANSWER = 2
MAX_SCORE = 10
CORE_ERROR_SCORE = 2
CORE_ERROR_WITH_COVERED_SCORE = 3

# (верхняя граница D, не включая; балл). D = 1 обрабатывается отдельно.
_COVERAGE_BANDS: tuple[tuple[Fraction, int], ...] = (
    (Fraction(3, 10), 3),
    (Fraction(1, 2), 4),
    (Fraction(13, 20), 5),
    (Fraction(4, 5), 6),
    (Fraction(1), 8),
)
_ZERO_COVERAGE_SCORE = 2
_FULL_COVERAGE_SCORE = 9


def _statuses(points: list[Any]) -> list[str] | None:
    out: list[str] = []
    for item in points:
        if not isinstance(item, dict):
            return None
        status = str(item.get("status", "")).strip().lower()
        if status not in POINT_STATUSES:
            return None
        out.append(status)
    return out


def _core_flags(errors: list[Any]) -> list[bool] | None:
    out: list[bool] = []
    for item in errors:
        if not isinstance(item, dict) or not isinstance(item.get("core"), bool):
            return None
        out.append(item["core"])
    return out


def _coverage_score(statuses: list[str]) -> int:
    covered = statuses.count("covered")
    partial = statuses.count("partial")
    coverage = Fraction(2 * covered + partial, 2 * len(statuses))
    if coverage == 0:
        return _ZERO_COVERAGE_SCORE
    for upper, score in _COVERAGE_BANDS:
        if coverage < upper:
            return score
    return _FULL_COVERAGE_SCORE


def has_bonus(bonus_marks: list[Any] | None, bonus_flag: bool | None) -> bool:
    """+1 даёт хотя бы один covered бонусный пункт; флаг bonus модели — только если
    бонусной разметки нет (у вопроса нет bonus_points или разметка не сопоставилась)."""
    statuses = _statuses(bonus_marks or [])
    if statuses:
        return "covered" in statuses
    return bool(bonus_flag)


def compute_score(
    points: list[Any],
    errors: list[Any],
    *,
    bonus: bool | None,
    off_topic: bool | None,
    bonus_marks: list[Any] | None = None,
) -> int | None:
    """Балл 1–10 для intent=answer или None, если разбор пустой или битый.

    D считается только по обязательным points; бонусные пункты (bonus_marks) в D не входят.
    None означает «посчитать нельзя» — вызывающий сам решает, чем заменить.
    """
    if off_topic:
        return MIN_SCORE

    statuses = _statuses(points or [])
    core_flags = _core_flags(errors or [])
    if not statuses or core_flags is None:
        return None

    if any(core_flags):
        return CORE_ERROR_WITH_COVERED_SCORE if "covered" in statuses else CORE_ERROR_SCORE

    score = _coverage_score(statuses) + (1 if has_bonus(bonus_marks, bonus) else 0)
    score -= len(core_flags)  # здесь остались только некритичные ошибки
    return max(MIN_SCORED_ANSWER, min(MAX_SCORE, score))
