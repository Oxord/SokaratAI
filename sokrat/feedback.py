"""Обратная связь тестеров по отчёту: оценка 1–5, «как на реальном собесе?», комментарий.

Пишется построчно в DATA_DIR/feedback.jsonl (по одной записи на отчёт — повторная
отправка перезаписывает предыдущую). Нужна для метрик пилота из ROADMAP, раздел 6.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR

FEEDBACK_PATH: Path = Path(os.getenv("FEEDBACK_PATH") or DATA_DIR / "feedback.jsonl")
_MAX_COMMENT = 2000
_lock = threading.Lock()


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def save_feedback(
    *,
    session_id: str,
    user: str,
    rating: int,
    realism: int | None = None,
    comment: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    """Сохраняет отзыв; для той же пары (сессия, пользователь) заменяет прежний."""
    store = path or FEEDBACK_PATH
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session_id": session_id,
        "user_id": user,
        "rating": int(rating),
        "realism": int(realism) if realism else None,
        "comment": (comment or "").strip()[:_MAX_COMMENT],
    }
    with _lock:
        rest = [
            r for r in _read(store)
            if not (r.get("session_id") == session_id and r.get("user_id") == user)
        ]
        rest.append(record)
        store.parent.mkdir(parents=True, exist_ok=True)
        tmp = store.with_suffix(".tmp")
        tmp.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rest) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, store)
    return record


def delete_user_feedback(user: str, *, path: Path | None = None) -> int:
    """Удаляет все отзывы пользователя (при удалении данных). Возвращает число записей."""
    store = path or FEEDBACK_PATH
    if not user:
        return 0
    with _lock:
        records = _read(store)
        kept = [r for r in records if r.get("user_id") != user]
        if len(kept) == len(records):
            return 0
        tmp = store.with_suffix(".tmp")
        tmp.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept),
            encoding="utf-8",
        )
        os.replace(tmp, store)
    return len(records) - len(kept)


def summary(path: Path | None = None) -> dict[str, Any]:
    """Сводка для владельца: число отзывов и средние оценки."""
    records = _read(path or FEEDBACK_PATH)

    def avg(key: str) -> float | None:
        vals = [r[key] for r in records if isinstance(r.get(key), int)]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {"count": len(records), "avg_rating": avg("rating"), "avg_realism": avg("realism")}


def recent_comments(limit: int = 20, *, path: Path | None = None) -> list[dict[str, Any]]:
    """Последние отзывы с комментарием (без логина пользователя)."""
    rows = [r for r in _read(path or FEEDBACK_PATH) if r.get("comment")]
    rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return [
        {"ts": r.get("ts"), "rating": r.get("rating"), "realism": r.get("realism"), "comment": r["comment"]}
        for r in rows[:limit]
    ]
