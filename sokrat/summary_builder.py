"""Shared summary builder — used by app.py and the LangGraph summary node.
Pure logic: takes per-question scores and produces an aggregated report dict
(scores, dimensions, skills coverage, recommendation)."""
from __future__ import annotations

from .analyzer import DIMENSION_LABELS
from .score_bands import (
    EXCELLENT,
    MIN_ANSWERED_SHARE,
    SUMMARY_VERSION,
    VERDICT_COMPETENT_MIN,
    VERDICT_STRONG_MIN,
    band,
)

INSUFFICIENT = "Insufficient"
INSUFFICIENT_LABEL = "Недостаточно ответов для вывода"

RECOMMENDATION_TEMPLATES: dict[str, str] = {
    "Strong": (
        "Отличный результат! Ты уверенно ответил на большинство вопросов. "
        "Ты хорошо подготовлен к этому уровню. "
        "Сосредоточься на паре слабых мест из списка выше — и будешь готов к реальному интервью."
    ),
    "Competent": (
        "Хорошая база. Ты понимаешь ключевые концепции, но есть области для роста. "
        "Проработай темы из раздела «Над чем поработать» — это значительно укрепит твою позицию. "
        "Продолжай практиковаться, и скоро выйдешь на уверенный уровень."
    ),
    "Needs Work": (
        "Есть над чем поработать. Не расстраивайся — это именно то, для чего и создан Сократ. "
        "Повтори фундаментальные концепции по темам из раздела «Над чем поработать». "
        "Попробуй пройти интервью ещё раз через несколько дней — результат улучшится."
    ),
    INSUFFICIENT: (
        "Слишком много вопросов осталось без ответа, чтобы делать вывод об уровне. "
        "Пропуски и «не знаю» считаются как 0, поэтому процент низкий. "
        "Пройди интервью ещё раз и постарайся ответить хотя бы коротко на каждый вопрос."
    ),
}

VERDICT_LABELS: dict[str, str] = {
    "Strong": "Сильный результат",
    "Competent": "Уверенная база",
    "Needs Work": "Есть над чем поработать",
    INSUFFICIENT: INSUFFICIENT_LABEL,
}


def compute_dimension_averages(scores: list[dict]) -> dict[str, float]:
    averages: dict[str, float] = {}
    for key in DIMENSION_LABELS:
        values = [s.get(key) for s in scores if isinstance(s.get(key), int)]
        averages[key] = round(sum(values) / len(values), 1) if values else 0.0
    return averages


def compute_skills_coverage(
    required_skills: list[str], scores: list[dict]
) -> list[dict]:
    """Покрытие навыков по вопросам, которые их проверяют (skills_touched).

    Пропуск и «не знаю» по навыку входят в среднее как 0. Навык без единого
    вопроса помечается tested=False и в среднее не попадает.
    """
    if not required_skills:
        return []
    coverage: list[dict] = []
    for skill in required_skills:
        key = skill.lower()
        values = [
            int(score.get("score") or 0)
            for score in scores
            if any(str(t).lower() == key for t in score.get("skills_touched") or [])
        ]
        coverage.append(
            {
                "skill": skill,
                "questions_count": len(values),
                "answered_count": sum(1 for v in values if v > 0),
                "avg_score": round(sum(values) / len(values), 1) if values else 0.0,
                "tested": bool(values),
            }
        )
    return coverage


def _verdict(percentage: float, asked: int, answered: int) -> str:
    if asked == 0 or answered / asked < MIN_ANSWERED_SHARE:
        return INSUFFICIENT
    overall = percentage / 10
    if overall >= VERDICT_STRONG_MIN:
        return "Strong"
    if overall >= VERDICT_COMPETENT_MIN:
        return "Competent"
    return "Needs Work"


def compute_summary(
    role: str,
    level: str,
    interview_type: str,
    questions: list[dict],
    answers: list[str],
    scores: list[dict],
    required_skills: list[str] | None = None,
) -> dict:
    """Итоги интервью. Знаменатель — все заданные вопросы: пропуск и «не знаю» = 0."""
    values = [int(s.get("score") or 0) for s in scores]
    asked = len(values)
    answered = [v for v in values if v > 0]
    total = sum(values)
    percentage = round(total / (asked * 10) * 100, 1) if asked else 0.0
    avg_answered = sum(answered) / len(answered) if answered else 0.0
    verdict = _verdict(percentage, asked, len(answered))

    def texts(predicate) -> list[str]:
        return [
            questions[i]["question"]
            for i, v in enumerate(values)
            if predicate(v) and i < len(questions)
        ]

    strengths = texts(lambda v: band(v) == EXCELLENT)
    improvements = texts(lambda v: v > 0 and band(v) != EXCELLENT)

    elapsed_values = [
        s["elapsed_seconds"]
        for s in scores
        if isinstance(s.get("elapsed_seconds"), (int, float))
    ]
    total_time = sum(elapsed_values)
    avg_time = total_time / len(elapsed_values) if elapsed_values else 0.0

    return {
        "summary_version": SUMMARY_VERSION,
        "asked_count": asked,
        "answered_count": len(answered),
        "answered_share": round(len(answered) / asked, 2) if asked else 0.0,
        "avg_score_answered": round(avg_answered, 1),
        # Прежние поля: app.py и сохранённые сессии читают их по этим именам.
        "avg_score": round(avg_answered, 1),
        "total_score": total,
        "max_possible": asked * 10,
        "percentage": percentage,
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS[verdict],
        "strengths": strengths,
        "improvements": improvements,
        "skipped_count": asked - len(answered),
        "counted_count": len(answered),
        "recommendation": RECOMMENDATION_TEMPLATES[verdict],
        "per_question": scores,
        "skills_coverage": compute_skills_coverage(required_skills or [], scores),
        "dimensions": compute_dimension_averages(scores),
        "total_time_seconds": round(total_time, 1),
        "avg_time_seconds": round(avg_time, 1),
    }
