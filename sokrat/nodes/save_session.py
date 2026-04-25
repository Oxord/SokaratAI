from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..config import SESSIONS_DIR
from ..state import InterviewState
from ..utils.errors import format_error

VOLATILE_FIELDS: tuple[str, ...] = (
    "user_input",
    "assistant_output",
    "current_question",
    "elapsed_seconds",
    "clarifications_used",
    "last_analysis",
)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def save_session_node(state: InterviewState) -> dict[str, Any]:
    """Persist the finished session to data/sessions/<id>.json.

    Strips per-turn volatile fields so the saved record is a clean transcript
    plus aggregated summary, not a snapshot of in-flight chat state.
    """
    errors = list(state.get("errors") or [])
    session_id = state.get("session_id") or ""
    if not session_id:
        errors.append("save_session: missing session_id")
        return {
            "current_step": "done",
            "errors": errors,
        }

    payload: dict[str, Any] = {}
    for key, value in state.items():
        if key in VOLATILE_FIELDS:
            continue
        payload[key] = value

    target = SESSIONS_DIR / f"{session_id}.json"
    try:
        _atomic_write_json(target, payload)
        saved_path = str(target)
    except Exception as exc:  # noqa: BLE001
        errors.append(format_error("save_session_error", exc))
        saved_path = ""

    return {
        "current_step": "done",
        "saved_path": saved_path,
        "errors": errors,
    }
