"""Индекс готовности к вакансии: прозрачная взвешенная оценка 0–100.

Индекс складывается только из интервью (качество ответов P и владение
проверенными навыками S). Соответствие резюме вакансии (R) — отдельная строка,
в индекс не входит. При малом числе оценённых ответов вместо числа отдаётся
диапазон с пометкой «предварительно».
"""
from __future__ import annotations

from typing import Any

from .gap_analysis import GapAnalysis
from .score_bands import (
    READINESS_MIN_ANSWERS,
    READINESS_RANGE_STEP,
    READINESS_ROUND_STEP,
    READINESS_ROUND_STEP_ROUGH,
    ceil_to,
    floor_to,
    round_to,
)

# Веса — перенормировка исходных 0.40 / 0.35 (без резюме) на сумму 1; не откалиброваны.
W_PERFORMANCE = 0.53
W_SKILLS = 0.47

READINESS_VERSION = 2

_LABELS: list[tuple[int, str]] = [
    (75, "Готов к собеседованию"),
    (50, "Почти готов"),
    (0, "Нужна подготовка"),
]


def label_for_index(index: int) -> str:
    for threshold, label in _LABELS:
        if index >= threshold:
            return label
    return _LABELS[-1][1]


def _components(summary: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = [
        {
            "key": "performance",
            "name": "Качество ответов на интервью",
            "value": float(summary.get("percentage") or 0.0),
            "weight": W_PERFORMANCE,
        }
    ]
    tested = [e for e in summary.get("skills_coverage") or [] if e.get("tested")]
    if tested:
        components.append(
            {
                "key": "skills",
                "name": "Владение проверенными навыками",
                "value": sum(float(e.get("avg_score") or 0.0) * 10.0 for e in tested) / len(tested),
                "weight": W_SKILLS,
            }
        )
    return components


def _preliminary_range(index: float, asked: int, missing: int) -> list[int]:
    """Индекс, если бы недостающие ответы были все на 0 или все на 10."""
    total = asked + missing
    low = index * asked / total
    high = (index * asked + 100.0 * missing) / total
    return [
        max(0, floor_to(low, READINESS_RANGE_STEP)),
        min(100, ceil_to(high, READINESS_RANGE_STEP)),
    ]


def compute_readiness(
    summary: dict[str, Any],
    gaps: GapAnalysis | None = None,
    min_answers: int = READINESS_MIN_ANSWERS,
) -> dict[str, Any]:
    """Индекс готовности по итогам интервью.

    `min_answers` — сколько оценённых ответов нужно для числа (для короткого
    интервью — число его вопросов, но не больше READINESS_MIN_ANSWERS).
    """
    summary = summary or {}
    asked = int(summary.get("asked_count") or 0)
    answered = int(summary.get("answered_count") or 0)
    required = max(1, min(READINESS_MIN_ANSWERS, min_answers))

    components = _components(summary)
    total_weight = sum(c["weight"] for c in components)
    raw_index = sum(c["value"] * c["weight"] for c in components) / total_weight

    result: dict[str, Any] = {
        "version": READINESS_VERSION,
        "index": None,
        "label": None,
        "preliminary": answered < required,
        "range": None,
        "answers_needed": max(0, required - answered),
        "precision": None,
        "answered_count": answered,
        "untested_skills": [
            e["skill"] for e in summary.get("skills_coverage") or [] if not e.get("tested")
        ],
        "resume_coverage_pct": gaps.coverage_pct if gaps is not None and gaps.total > 0 else None,
    }

    if result["preliminary"]:
        result["range"] = _preliminary_range(raw_index, asked, result["answers_needed"])
    elif answered < READINESS_MIN_ANSWERS:
        # Короткое интервью: число есть, но грубое и без ярлыка «Готов».
        result["precision"] = "rough"
        result["index"] = max(0, min(100, round_to(raw_index, READINESS_ROUND_STEP_ROUGH)))
    else:
        result["precision"] = "normal"
        index = max(0, min(100, round_to(raw_index, READINESS_ROUND_STEP)))
        result["index"] = index
        result["label"] = label_for_index(index)

    result["components"] = [
        {
            "key": c["key"],
            "name": c["name"],
            "value": round(c["value"], 1),
            "weight_pct": int(round(100 * c["weight"] / total_weight)),
        }
        for c in components
    ]
    return result
