"""Фиксированные эталонные пункты вопроса (key_points).

Пункты задаются заранее (офлайн-скриптом или человеком), судья их не придумывает,
а только размечает covered/partial/missing — так число пунктов не плавает между повторами.
"""
from __future__ import annotations

import re
from typing import Any

MIN_POINTS = 3
MAX_POINTS = 6
# Детали, без которых сильный ответ возможен: в D не входят, дают только бонус.
MAX_BONUS_POINTS = 3
KEY_POINTS_SOURCES: tuple[str, ...] = ("llm", "human")

_SPACES_RE = re.compile(r"\s+")


def _dedup_key(text: str) -> str:
    return _SPACES_RE.sub(" ", text).strip().lower()


def _clean_points(value: Any, lo: int, hi: int) -> list[str] | None:
    if not isinstance(value, list):
        return None
    points = [_SPACES_RE.sub(" ", str(item)).strip() for item in value if isinstance(item, str)]
    if len(points) != len(value) or any(not p for p in points):
        return None
    if len({_dedup_key(p) for p in points}) != len(points):
        return None
    if not lo <= len(points) <= hi:
        return None
    return points


def validate_key_points(value: Any) -> list[str] | None:
    """Список из 3–6 непустых различных строк или None, если формат не подходит."""
    return _clean_points(value, MIN_POINTS, MAX_POINTS)


def validate_bonus_points(value: Any, key_points: list[str]) -> list[str] | None:
    """0–3 дополнительных пункта, не совпадающих с обязательными; отсутствие поля — []."""
    if value is None:
        return []
    bonus = _clean_points(value, 0, MAX_BONUS_POINTS)
    if bonus is None:
        return None
    if {_dedup_key(p) for p in bonus} & {_dedup_key(p) for p in key_points}:
        return None
    return bonus


BONUS_PREFIX = "B"


def format_fixed_points(points: list[str] | None, prefix: str = "") -> str:
    """Нумерованный список для промпта судьи; номер = поле i в разметке (B1.. для бонусных)."""
    if not points:
        return "(не заданы)"
    return "\n".join(f"{prefix}{n}. {p}" for n, p in enumerate(points, 1))


def _point_index(item: Any, prefix: str = "") -> int | None:
    if not isinstance(item, dict):
        return None
    raw = item.get("i")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if prefix and text[:1].upper() == prefix:
        text = text[1:]
    return int(text) if text.isdigit() else None


def _apply(marks: Any, points: list[str], prefix: str) -> list[dict[str, Any]] | None:
    if not isinstance(marks, list) or len(marks) != len(points):
        return None
    by_index: dict[int, dict[str, Any]] = {}
    for item in marks:
        index = _point_index(item, prefix)
        if index is None or not 1 <= index <= len(points) or index in by_index:
            return None
        by_index[index] = item
    return [
        {
            "point": points[n - 1],
            "status": by_index[n].get("status"),
            "evidence": by_index[n].get("evidence", ""),
        }
        for n in range(1, len(points) + 1)
    ]


def apply_fixed_points(points: Any, fixed: list[str]) -> list[dict[str, Any]] | None:
    """Сопоставляет компактную разметку {i, status, evidence} с фиксированными пунктами.

    Возвращает полный разбор [{point, status, evidence}] в порядке пунктов или None,
    если номера не покрывают 1..N ровно по одному разу.
    """
    return _apply(points, fixed, "")


def apply_bonus_points(marks: Any, bonus: list[str]) -> list[dict[str, Any]] | None:
    """То же для бонусных пунктов: номера B1..Bk (допускается и просто 1..k)."""
    return _apply(marks, bonus, BONUS_PREFIX)
