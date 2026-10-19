"""История сессий и динамика прогресса по одной роли.

Сессии лежат файлами в SESSIONS_DIR. Поддерживаются обе формы записи: прежняя
(created_at + summary + qa_pairs) и текущая (снимок состояния графа).
Идентификации пользователей пока нет, поэтому история — общая для установки.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .analyzer import DIMENSION_LABELS
from .config import SESSIONS_DIR
from .score_bands import SUMMARY_VERSION, is_weak

log = logging.getLogger(__name__)

_MAX_FILES = 500
_RECENT_LIMIT = 6


def _timestamp(data: dict[str, Any]) -> str:
    return str(data.get("created_at") or data.get("finished_at") or data.get("started_at") or "")


def _normalize(data: dict[str, Any]) -> dict[str, Any] | None:
    summary = data.get("summary")
    if not isinstance(summary, dict) or summary.get("percentage") is None:
        return None
    counted = summary.get("counted_count")
    if counted == 0:
        return None
    readiness = summary.get("readiness") or {}
    job = data.get("job_vacancy") or {}
    return {
        "session_id": str(data.get("session_id") or ""),
        "user_id": str(data.get("user_id") or ""),
        "ts": _timestamp(data),
        "role": str(data.get("role") or ""),
        "level": str(data.get("level") or ""),
        "company": str(job.get("company") or ""),
        "percentage": float(summary.get("percentage") or 0.0),
        "dimensions": summary.get("dimensions") or {},
        "readiness": readiness.get("index"),
        "summary_version": int(summary.get("summary_version") or 1),
    }


def load_sessions(sessions_dir: Path | None = None) -> list[dict[str, Any]]:
    """Все пригодные сессии, от старых к новым. Битые файлы пропускаются."""
    directory = sessions_dir or SESSIONS_DIR
    if not directory.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"))[:_MAX_FILES]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.debug("skip unreadable session %s", path, exc_info=True)
            continue
        if isinstance(data, dict):
            record = _normalize(data)
            if record and record["ts"]:
                records.append(record)
    records.sort(key=lambda r: r["ts"])
    return records


def build_dynamics(
    current: dict[str, Any],
    role: str,
    session_id: str,
    sessions_dir: Path | None = None,
    user_id: str = "",
) -> dict[str, Any] | None:
    """Сравнивает текущий результат с прошлыми сессиями по той же роли.

    `current` — summary текущей сессии. Сравниваются только сессии с той же
    версией формулы итогов. Возвращает None, если прошлых сессий нет.
    """
    version = int(current.get("summary_version") or 1)
    previous = [
        r
        for r in load_sessions(sessions_dir)
        if r["role"].strip().lower() == role.strip().lower()
        and r["session_id"] != session_id
        and r["user_id"] == user_id
        and r["summary_version"] == version
    ][-_RECENT_LIMIT:]
    if not previous:
        return None

    now_pct = float(current.get("percentage") or 0.0)
    last = previous[-1]
    first = previous[0]
    dim_changes: list[dict[str, Any]] = []
    now_dims = current.get("dimensions") or {}
    for key, label in DIMENSION_LABELS.items():
        before = float((last["dimensions"] or {}).get(key) or 0.0)
        after = float(now_dims.get(key) or 0.0)
        if before > 0 and after > 0:
            dim_changes.append(
                {"key": key, "label": label, "before": before, "after": after,
                 "delta": round(after - before, 1)}
            )

    return {
        "sessions_count": len(previous) + 1,
        "points": [r["percentage"] for r in previous] + [now_pct],
        "delta_vs_last": round(now_pct - last["percentage"], 1),
        "delta_vs_first": round(now_pct - first["percentage"], 1),
        "best_before": max(r["percentage"] for r in previous),
        "dimension_changes": dim_changes,
    }


def format_dynamics(dyn: dict[str, Any]) -> str:
    def arrow(delta: float) -> str:
        return "📈" if delta > 0.5 else "📉" if delta < -0.5 else "➖"

    lines = [
        "### 📈 Динамика по этой роли",
        f"- Сессий всего: **{dyn['sessions_count']}** · к прошлой: "
        f"{arrow(dyn['delta_vs_last'])} **{dyn['delta_vs_last']:+g} п.п.**",
    ]
    if dyn["sessions_count"] > 2:
        lines.append(
            f"- С первой сессии: {arrow(dyn['delta_vs_first'])} "
            f"**{dyn['delta_vs_first']:+g} п.п.** · лучший прошлый результат: "
            f"**{dyn['best_before']:g}%**"
        )
    changes = sorted(dyn["dimension_changes"], key=lambda c: c["delta"])
    if changes:
        worst, best = changes[0], changes[-1]
        if best["delta"] > 0.5:
            lines.append(f"- Сильнее всего выросло: **{best['label']}** ({best['delta']:+g})")
        if worst["delta"] < -0.5:
            lines.append(f"- Просело: **{worst['label']}** ({worst['delta']:+g})")
    return "\n".join(lines) + "\n\n"


def recent_weak_topics(
    role: str,
    user_id: str = "",
    limit: int = 5,
    sessions_dir: Path | None = None,
) -> list[str]:
    """Вопросы, на которых пользователь проседал в прошлых сессиях по роли.

    Новые сессии (вопросы и оценки по индексам) и прежние (qa_pairs) читаются
    одинаково. Свежие слабые ответы идут первыми, дубли убираются.
    """
    directory = sessions_dir or SESSIONS_DIR
    if not directory.is_dir():
        return []
    wanted = role.strip().lower()
    found: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.json"))[:_MAX_FILES]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("role") or "").strip().lower() != wanted:
            continue
        if str(data.get("user_id") or "") != user_id:
            continue
        ts = _timestamp(data)
        pairs: list[tuple[str, int]] = []
        if isinstance(data.get("qa_pairs"), list):
            for qa in data["qa_pairs"]:
                if isinstance(qa, dict):
                    pairs.append((str(qa.get("question_text") or ""), int(qa.get("score") or 0)))
        else:
            questions = data.get("questions") or []
            scores = (data.get("scores") or [])
            for q, sc in zip(questions, scores):
                if isinstance(q, dict) and isinstance(sc, dict):
                    pairs.append((str(q.get("question") or ""), int(sc.get("score") or 0)))
        for text, score in pairs:
            if text and is_weak(score):
                found.append((ts, text.strip()))
    found.sort(key=lambda item: item[0], reverse=True)
    topics: list[str] = []
    for _, text in found:
        if text.lower() not in {t.lower() for t in topics}:
            topics.append(text)
        if len(topics) >= limit:
            break
    return topics


def other_vacancies(
    user_id: str,
    current_company: str,
    session_id: str,
    sessions_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Последний индекс готовности по другим компаниям пользователя (для сравнения).

    Только сессии текущей формулы итогов: индексы старой формулы включали резюме.
    """
    latest: dict[str, dict[str, Any]] = {}
    for r in load_sessions(sessions_dir):
        if r["user_id"] != user_id or r["session_id"] == session_id:
            continue
        if r["summary_version"] != SUMMARY_VERSION:
            continue
        company = r["company"].strip()
        if not company or company.lower() == (current_company or "").strip().lower():
            continue
        if r["readiness"] is None:
            continue
        latest[company.lower()] = {"company": company, "readiness": r["readiness"], "role": r["role"]}
    return sorted(latest.values(), key=lambda x: x["readiness"], reverse=True)[:5]
