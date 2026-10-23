"""Оценки вопросов тестерами (👍/👎 в отчёте) — основа для отсева слабых вопросов (D5).

Хранится в DATA_DIR/question_ratings.jsonl: одна запись на (сессия, вопрос, пользователь),
повторная оценка заменяет прежнюю. Вопрос идентифицируется хэшем нормализованного текста.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR

RATINGS_PATH: Path = Path(os.getenv("QUESTION_RATINGS_PATH") or DATA_DIR / "question_ratings.jsonl")
_lock = threading.Lock()


def question_key(text: str) -> str:
    norm = " ".join((text or "").lower().split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    os.replace(tmp, path)


def rate_question(
    *, session_id: str, user: str, question: str, value: int, role: str = "", path: Path | None = None
) -> None:
    """value: 1 — полезный вопрос, -1 — слабый/неуместный."""
    store = path or RATINGS_PATH
    key = question_key(question)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session_id": session_id,
        "user_id": user,
        "qkey": key,
        "question": (question or "").strip()[:400],
        "role": (role or "")[:100],
        "value": 1 if value > 0 else -1,
    }
    with _lock:
        rest = [
            r for r in _read(store)
            if not (r.get("session_id") == session_id and r.get("user_id") == user and r.get("qkey") == key)
        ]
        rest.append(record)
        _write(store, rest)


def delete_user_ratings(user: str, *, path: Path | None = None) -> int:
    store = path or RATINGS_PATH
    if not user:
        return 0
    with _lock:
        records = _read(store)
        kept = [r for r in records if r.get("user_id") != user]
        if len(kept) != len(records):
            _write(store, kept)
    return len(records) - len(kept)


def worst_questions(limit: int = 10, *, path: Path | None = None) -> list[dict[str, Any]]:
    """Вопросы с наибольшим перевесом 👎: кандидаты на ручную проверку."""
    agg: dict[str, dict[str, Any]] = {}
    for r in _read(path or RATINGS_PATH):
        row = agg.setdefault(
            r.get("qkey", ""),
            {"question": r.get("question", ""), "role": r.get("role", ""), "up": 0, "down": 0},
        )
        row["up" if r.get("value", 0) > 0 else "down"] += 1
    rows = [r for r in agg.values() if r["down"] > r["up"]]
    rows.sort(key=lambda r: (r["up"] - r["down"], -r["down"]))
    return rows[:limit]
