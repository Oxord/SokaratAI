"""Shared summary builder — used by app.py and the LangGraph summary node.
Pure logic: takes per-question scores and produces an aggregated report dict
(scores, dimensions, skills coverage, recommendation)."""
from __future__ import annotations

from .analyzer import DIMENSION_LABELS

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
    if not required_skills:
        return []
    coverage: list[dict] = []
    for skill in required_skills:
        hits: list[int] = []
        for score in scores:
            touched = score.get("skills_touched") or []
            if any(t.lower() == skill.lower() for t in touched) and score.get("score", 0) > 0:
                hits.append(score["score"])
        avg = round(sum(hits) / len(hits), 1) if hits else 0.0
        coverage.append(
            {
                "skill": skill,
                "questions_count": len(hits),
                "avg_score": avg,
            }
        )
    return coverage


def compute_summary(
    role: str,
    level: str,
    interview_type: str,
    questions: list[dict],
    answers: list[str],
    scores: list[dict],
    required_skills: list[str] | None = None,
) -> dict:
    counted = [s["score"] for s in scores if s["score"] > 0]
    skipped_count = sum(1 for s in scores if s["score"] == 0)
    total = sum(counted)
    max_possible = len(counted) * 10
    avg = total / len(counted) if counted else 0
    percentage = round(avg / 10 * 100, 1) if counted else 0.0

    if not counted:
        verdict = "Needs Work"
    elif avg >= 8.0:
        verdict = "Strong"
    elif avg >= 6.5:
        verdict = "Competent"
    else:
        verdict = "Needs Work"

    strengths = [
        questions[i]["question"]
        for i, s in enumerate(scores)
        if s["score"] >= 8 and i < len(questions)
    ]
    improvements = [
        questions[i]["question"]
        for i, s in enumerate(scores)
        if 0 < s["score"] <= 6 and i < len(questions)
    ]

    elapsed_values = [
        s["elapsed_seconds"]
        for s in scores
        if isinstance(s.get("elapsed_seconds"), (int, float))
    ]
    total_time = sum(elapsed_values)
    avg_time = total_time / len(elapsed_values) if elapsed_values else 0.0

    return {
        "avg_score": round(avg, 1),
        "total_score": total,
        "max_possible": max_possible,
        "percentage": percentage,
        "verdict": verdict,
        "strengths": strengths,
        "improvements": improvements,
        "skipped_count": skipped_count,
        "counted_count": len(counted),
        "recommendation": RECOMMENDATION_TEMPLATES[verdict],
        "per_question": scores,
        "skills_coverage": compute_skills_coverage(required_skills or [], scores),
        "dimensions": compute_dimension_averages(scores),
        "total_time_seconds": round(total_time, 1),
        "avg_time_seconds": round(avg_time, 1),
    }
