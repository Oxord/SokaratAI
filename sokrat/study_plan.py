"""План подготовки на N дней по итогам интервью."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage

from .llm import extract_text, get_chat_model_for
from .prompts import load_prompt
from .vacancy_parser import _parse_json_object

log = logging.getLogger(__name__)

MIN_DAYS = 3
MAX_DAYS = 14
_MAX_TASKS = 4


class StudyPlanError(RuntimeError):
    pass


@dataclass
class StudyDay:
    day: int
    focus: str
    tasks: list[str] = field(default_factory=list)
    minutes: int = 60
    checkpoint: str = ""


def collect_focus_areas(
    summary: dict[str, Any], gaps: dict[str, Any] | None, weak_topics: list[dict]
) -> list[str]:
    """Единый приоритизированный список тем: пробелы резюме, слабые навыки, слабые ответы."""
    areas: list[str] = []

    def add(item: str) -> None:
        item = item.strip()
        if item and item.lower() not in {a.lower() for a in areas}:
            areas.append(item)

    for skill in (gaps or {}).get("missing", []):
        add(f"{skill} (нет в резюме, требуется вакансией)")
    for entry in summary.get("skills_coverage") or []:
        if entry.get("questions_count", 0) == 0:
            add(f"{entry.get('skill', '')} (не раскрыт на интервью)")
        elif (entry.get("avg_score") or 0) < 6:
            add(f"{entry.get('skill', '')} (средний балл {entry.get('avg_score')}/10)")
    star = summary.get("star") or {}
    if star and star.get("avg_completeness_pct", 100) < 75:
        add("Структура поведенческих ответов по STAR")
    for topic in weak_topics:
        add(str(topic.get("topic", "")))
    for skill in (gaps or {}).get("weak_evidence", []):
        add(f"{skill} (указан, но без примеров из опыта)")
    return areas[:10]


def build_study_plan(
    role: str,
    level: str,
    days: int,
    focus_areas: list[str],
    company_context: str = "",
) -> list[StudyDay]:
    days = max(MIN_DAYS, min(MAX_DAYS, int(days)))
    if not focus_areas:
        raise StudyPlanError("нет слабых мест, под которые можно составить план")

    prompt = load_prompt("study_plan").format(
        role=role,
        level=level,
        days=days,
        focus_areas="\n".join(f"- {a}" for a in focus_areas),
        company_context=company_context.strip() or "(нет)",
    )
    try:
        model = get_chat_model_for("general", temperature=0.4)
        response = model.invoke([HumanMessage(content=prompt)])
        data = _parse_json_object(extract_text(response.content))
    except Exception as exc:
        raise StudyPlanError(f"LLM не смог составить план ({exc})") from exc

    raw_days = data.get("days")
    if not isinstance(raw_days, list) or not raw_days:
        raise StudyPlanError("модель вернула план в неверном формате")

    plan: list[StudyDay] = []
    for i, item in enumerate(raw_days[:days], 1):
        if not isinstance(item, dict):
            continue
        tasks = [str(t).strip()[:200] for t in (item.get("tasks") or []) if str(t).strip()]
        try:
            minutes = int(item.get("minutes") or 60)
        except (TypeError, ValueError):
            minutes = 60
        plan.append(
            StudyDay(
                day=i,
                focus=str(item.get("focus") or "").strip()[:120],
                tasks=tasks[:_MAX_TASKS],
                minutes=max(15, min(240, minutes)),
                checkpoint=str(item.get("checkpoint") or "").strip()[:200],
            )
        )
    plan = [d for d in plan if d.focus and d.tasks]
    if not plan:
        raise StudyPlanError("модель вернула пустой план")
    return plan


def format_study_plan(plan: list[StudyDay]) -> str:
    total = sum(d.minutes for d in plan)
    lines = [f"## 📅 План подготовки на {len(plan)} дн. (~{total // 60} ч {total % 60} мин)\n"]
    for d in plan:
        lines.append(f"### День {d.day} — {d.focus} · ⏱ {d.minutes} мин")
        lines.extend(f"- {t}" for t in d.tasks)
        if d.checkpoint:
            lines.append(f"- ✅ **Проверка:** {d.checkpoint}")
        lines.append("")
    lines.append(
        "_После плана пройди интервью заново — индекс готовности и динамика покажут прогресс._"
    )
    return "\n".join(lines)
