"""Удаление персональных данных пользователя (ориентир — 152-ФЗ).

Стираются: сохранённые сессии (в них ответы и структурированный профиль резюме),
отзывы, активные интервью в памяти и, по желанию, сама учётная запись.
Сессии без user_id (гостевые и старого формата) принадлежность не определяют — их не трогаем.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import cohorts, users
from .config import SESSIONS_DIR
from .feedback import delete_user_feedback
from .question_ratings import delete_user_ratings

log = logging.getLogger(__name__)


def delete_user_data(
    user: str,
    *,
    delete_account: bool = False,
    sessions_dir: Path | None = None,
    live_sessions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Возвращает счётчики удалённого. `live_sessions` — словарь активных интервью (id → объект)."""
    if not user:
        return {"sessions": 0, "feedback": 0, "account": False}

    directory = sessions_dir or SESSIONS_DIR
    removed = 0
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and str(data.get("user_id") or "") == user:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    log.warning("could not delete session file %s", path.name)

    if live_sessions is not None:
        for key in [k for k, v in list(live_sessions.items()) if getattr(v, "user_id", "") == user]:
            live_sessions.pop(key, None)

    feedback_removed = delete_user_feedback(user) + delete_user_ratings(user)
    cohorts.leave(user)
    account = users.delete(user) if delete_account else False
    return {"sessions": removed, "feedback": feedback_removed, "account": account}
