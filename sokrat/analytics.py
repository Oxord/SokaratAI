"""Аналитика по всем сессиям — «кабинет» для вузов, карьерных центров и HR.

Запуск:  python -m sokrat.analytics [--role "Python Developer"] [--out data/analytics.md]

Сводка агрегирована и не содержит ответов пользователей и персональных данных:
только распределение результатов и типичные пробелы.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from .analyzer import DIMENSION_LABELS
from .config import DATA_DIR, SESSIONS_DIR

_BUCKETS = ((0, 40, "до 40%"), (40, 65, "40–64%"), (65, 80, "65–79%"), (80, 101, "80% и выше"))


def _iter_sessions(sessions_dir: Path):
    for path in sorted(sessions_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("summary"), dict):
            if data["summary"].get("counted_count") == 0:
                continue
            if data["summary"].get("percentage") is None:
                continue
            yield data


def aggregate(sessions_dir: Path | None = None, role: str | None = None) -> dict[str, Any]:
    sessions = list(_iter_sessions(sessions_dir or SESSIONS_DIR))
    if role:
        wanted = role.strip().lower()
        sessions = [s for s in sessions if str(s.get("role", "")).strip().lower() == wanted]

    pcts = [float(s["summary"]["percentage"]) for s in sessions]
    dims: dict[str, list[float]] = {k: [] for k in DIMENSION_LABELS}
    weak_skills: Counter[str] = Counter()
    unseen_skills: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    users: set[str] = set()
    star_values: list[float] = []
    readiness_values: list[float] = []

    for s in sessions:
        summary = s["summary"]
        roles[str(s.get("role") or "—")] += 1
        if s.get("user_id"):
            users.add(str(s["user_id"]))
        for key in DIMENSION_LABELS:
            value = (summary.get("dimensions") or {}).get(key)
            if isinstance(value, (int, float)) and value > 0:
                dims[key].append(float(value))
        for entry in summary.get("skills_coverage") or []:
            skill = str(entry.get("skill") or "").strip()
            if not skill:
                continue
            if not entry.get("questions_count"):
                unseen_skills[skill] += 1
            elif (entry.get("avg_score") or 0) < 6:
                weak_skills[skill] += 1
        star = summary.get("star")
        if isinstance(star, dict) and star.get("avg_completeness_pct") is not None:
            star_values.append(float(star["avg_completeness_pct"]))
        readiness = summary.get("readiness")
        if isinstance(readiness, dict) and readiness.get("index") is not None:
            readiness_values.append(float(readiness["index"]))

    distribution = {
        label: sum(1 for p in pcts if lo <= p < hi) for lo, hi, label in _BUCKETS
    }
    return {
        "sessions": len(sessions),
        "unique_users": len(users),
        "avg_percentage": round(statistics.mean(pcts), 1) if pcts else 0.0,
        "median_percentage": round(statistics.median(pcts), 1) if pcts else 0.0,
        "distribution": distribution,
        "dimensions": {
            DIMENSION_LABELS[k]: round(statistics.mean(v), 1) for k, v in dims.items() if v
        },
        "weak_skills": weak_skills.most_common(8),
        "unseen_skills": unseen_skills.most_common(8),
        "roles": roles.most_common(8),
        "avg_star_pct": round(statistics.mean(star_values), 1) if star_values else None,
        "avg_readiness": round(statistics.mean(readiness_values), 1) if readiness_values else None,
    }


def render_markdown(data: dict[str, Any], role: str | None = None) -> str:
    title = f"# Сократ — аналитика по сессиям{f' ({role})' if role else ''}\n"
    if data["sessions"] == 0:
        return title + "\nПока нет завершённых сессий.\n"
    lines = [
        title,
        f"- Сессий: **{data['sessions']}**"
        + (f" · уникальных пользователей: **{data['unique_users']}**" if data["unique_users"] else ""),
        f"- Средний результат: **{data['avg_percentage']}%** (медиана {data['median_percentage']}%)",
    ]
    if data["avg_readiness"] is not None:
        lines.append(f"- Средний индекс готовности к вакансии: **{data['avg_readiness']}/100**")
    if data["avg_star_pct"] is not None:
        lines.append(f"- Полнота поведенческих ответов (STAR): **{data['avg_star_pct']}%**")

    lines += ["", "## Распределение результатов"]
    total = data["sessions"]
    for label, count in data["distribution"].items():
        lines.append(f"- {label}: **{count}** ({round(100 * count / total)}%)")

    if data["dimensions"]:
        lines += ["", "## Средний профиль навыков общения (0–10)"]
        for label, value in sorted(data["dimensions"].items(), key=lambda x: x[1]):
            lines.append(f"- {label}: **{value}**")
    if data["weak_skills"]:
        lines += ["", "## Навыки, на которых чаще всего проседают"]
        lines += [f"- {skill}: {count} сесс." for skill, count in data["weak_skills"]]
    if data["unseen_skills"]:
        lines += ["", "## Требуемые навыки, которые не удалось раскрыть"]
        lines += [f"- {skill}: {count} сесс." for skill, count in data["unseen_skills"]]
    if data["roles"]:
        lines += ["", "## Популярные роли"]
        lines += [f"- {role_name}: {count}" for role_name, count in data["roles"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Сводная аналитика по сессиям Сократа")
    parser.add_argument("--role", help="фильтр по роли")
    parser.add_argument("--out", default=str(DATA_DIR / "analytics.md"))
    args = parser.parse_args()
    report = render_markdown(aggregate(role=args.role), args.role)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main()
