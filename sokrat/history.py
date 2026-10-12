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
        "ts": _timestamp(data),
        "role": str(data.get("role") or ""),
        "level": str(data.get("level") or ""),
        "company": str(job.get("company") or ""),
        "percentage": float(summary.get("percentage") or 0.0),
        "dimensions": summary.get("dimensions") or {},
        "readiness": readiness.get("index"),
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
) -> dict[str, Any] | None:
    """Сравнивает текущий результат с прошлыми сессиями по той же роли.

    `current` — summary текущей сессии. Возвращает None, если прошлых сессий нет.
    """
    previous = [
        r
        for r in load_sessions(sessions_dir)
        if r["role"].strip().lower() == role.strip().lower() and r["session_id"] != session_id
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
