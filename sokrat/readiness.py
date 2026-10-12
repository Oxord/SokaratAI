"""Индекс готовности к вакансии: прозрачная взвешенная оценка 0–100."""
from __future__ import annotations

from typing import Any

from .gap_analysis import GapAnalysis

_W_PERFORMANCE = 0.40
_W_SKILLS = 0.35
_W_RESUME = 0.25

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


def compute_readiness(summary: dict[str, Any], gaps: GapAnalysis | None = None) -> dict[str, Any]:
    """Считает индекс по доступным компонентам и пересчитывает веса на их сумму.

    Компоненты:
    - performance: общий процент баллов за интервью;
    - skills: средняя оценка по требуемым навыкам (нераскрытый навык = 0);
    - resume: доля требований вакансии, подтверждённых резюме.
    """
    if not summary or summary.get("counted_count", 0) == 0:
        return {"index": 0, "label": label_for_index(0), "components": []}

    components: list[dict[str, Any]] = [
        {
            "key": "performance",
            "name": "Качество ответов на интервью",
            "value": float(summary.get("percentage") or 0.0),
            "weight": _W_PERFORMANCE,
        }
    ]

    coverage = summary.get("skills_coverage") or []
    if coverage:
        per_skill = [
            (float(e.get("avg_score") or 0.0) * 10.0) if e.get("questions_count") else 0.0
            for e in coverage
        ]
        components.append(
            {
                "key": "skills",
                "name": "Владение требуемыми навыками",
                "value": sum(per_skill) / len(per_skill),
                "weight": _W_SKILLS,
            }
        )

    if gaps is not None and gaps.total > 0:
        components.append(
            {
                "key": "resume",
                "name": "Соответствие резюме вакансии",
                "value": gaps.coverage_pct,
                "weight": _W_RESUME,
            }
        )

    total_weight = sum(c["weight"] for c in components)
    index = sum(c["value"] * c["weight"] for c in components) / total_weight
    index_int = max(0, min(100, int(round(index))))

    for c in components:
        c["value"] = round(c["value"], 1)
        c["weight_pct"] = int(round(100 * c["weight"] / total_weight))
        del c["weight"]

    return {"index": index_int, "label": label_for_index(index_int), "components": components}
