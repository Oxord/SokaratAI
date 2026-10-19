"""Метрики пилота из ROADMAP (раздел 6), считаются по сохранённым сессиям.

  * доля сессий по реальной вакансии против абстрактной роли;
  * возвращаемость: доля пользователей с ≥2 сессиями;
  * прирост среднего балла между первой и третьей (или последней) сессией;
  * средняя длительность и число вопросов в сессии.
Ответы пользователей не читаются и не возвращаются.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SESSIONS_DIR


def _parse(ts: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts))
    except ValueError:
        return None


def pilot_stats(sessions_dir: Path | None = None) -> dict[str, Any]:
    directory = sessions_dir or SESSIONS_DIR
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        summary = data.get("summary") if isinstance(data, dict) else None
        if not isinstance(summary, dict) or summary.get("percentage") is None:
            continue
        if summary.get("counted_count") == 0:
            continue
        start, end = _parse(data.get("started_at")), _parse(data.get("finished_at"))
        minutes = (end - start).total_seconds() / 60 if start and end and end > start else None
        rows.append({
            "user": str(data.get("user_id") or ""),
            "ts": str(data.get("finished_at") or data.get("created_at") or ""),
            "pct": float(summary["percentage"]),
            "vacancy": bool(data.get("job_vacancy")),
            "questions": len(data.get("scores") or data.get("qa_pairs") or []),
            "minutes": minutes,
        })

    by_user: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["user"]:
            by_user.setdefault(r["user"], []).append(r)
    growth: list[float] = []
    for items in by_user.values():
        items.sort(key=lambda r: r["ts"])
        if len(items) >= 2:
            target = items[2] if len(items) >= 3 else items[-1]
            growth.append(target["pct"] - items[0]["pct"])

    def mean(values: list[float]) -> float | None:
        return round(statistics.mean(values), 1) if values else None

    n = len(rows)
    return {
        "sessions": n,
        "vacancy_share": round(100 * sum(r["vacancy"] for r in rows) / n, 1) if n else None,
        "users": len(by_user),
        "returning_share": (
            round(100 * sum(len(v) >= 2 for v in by_user.values()) / len(by_user), 1) if by_user else None
        ),
        "avg_growth_pp": mean(growth),
        "users_with_growth": len(growth),
        "avg_minutes": mean([r["minutes"] for r in rows if r["minutes"] is not None]),
        "avg_questions": mean([float(r["questions"]) for r in rows]),
    }
