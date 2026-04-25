from __future__ import annotations

import json
import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import QUESTIONS_PATH, SESSIONS_DIR
from .state import InterviewState

VOLATILE_FIELDS: tuple[str, ...] = (
    "user_input",
    "assistant_output",
    "current_question",
    "current_step",
)

SCHEMA_VERSION: int = 1


@lru_cache(maxsize=1)
def load_questions() -> dict[str, Any]:
    with QUESTIONS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def get_fallback_question(
    role: str,
    level: str,
    interview_type: str,
    exclude: list[str],
) -> str | None:
    bank = load_questions()
    try:
        candidates = bank["roles"][role][level][interview_type]
    except KeyError:
        return None
    excluded = set(exclude)
    available = [q for q in candidates if q not in excluded]
    if not available:
        return None
    return random.choice(available)


def save_session(state: InterviewState) -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    for key, value in state.items():
        if key in VOLATILE_FIELDS:
            continue
        payload[key] = value
    target = SESSIONS_DIR / f"{state['session_id']}.json"
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return target
