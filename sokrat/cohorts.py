"""Группы (вуз, курс, карьерный центр) — B2B-кабинет.

Владелец (админ) создаёт группу и получает код; студенты вводят код в своём кабинете и
тем самым соглашаются, что преподаватель видит их результаты (логин, число сессий, баллы).
Ответы на вопросы и резюме преподавателю не показываются. Пользователь состоит не более
чем в одной группе. Хранится в DATA_DIR/cohorts.json.
"""
from __future__ import annotations

import json
import os
import secrets
import statistics
import threading
import time
from pathlib import Path
from typing import Any

from .analyzer import DIMENSION_LABELS
from .config import DATA_DIR
from .history import load_sessions

COHORTS_PATH: Path = Path(os.getenv("COHORTS_PATH") or DATA_DIR / "cohorts.json")
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без похожих O/0, I/1
_lock = threading.Lock()
_BUCKETS = ((0, 40, "до 40%"), (40, 65, "40–64%"), (65, 80, "65–79%"), (80, 101, "80% и выше"))


def _load(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _norm(code: str) -> str:
    return (code or "").strip().upper()


def create_cohort(name: str, owner: str, *, path: Path | None = None) -> str:
    store = path or COHORTS_PATH
    clean = " ".join((name or "").split())[:80]
    if not clean:
        raise ValueError("Укажи название группы.")
    with _lock:
        data = _load(store)
        code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
        while code in data:
            code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
        data[code] = {"name": clean, "owner": owner, "created": time.strftime("%Y-%m-%d"), "members": []}
        _save(store, data)
    return code


def list_cohorts(*, path: Path | None = None) -> list[dict[str, Any]]:
    data = _load(path or COHORTS_PATH)
    return [
        {"code": code, "name": c.get("name", ""), "created": c.get("created", ""), "members": len(c.get("members", []))}
        for code, c in sorted(data.items(), key=lambda kv: kv[1].get("created", ""), reverse=True)
    ]


def user_cohort(user: str, *, path: Path | None = None) -> dict[str, str] | None:
    for code, c in _load(path or COHORTS_PATH).items():
        if user in c.get("members", []):
            return {"code": code, "name": c.get("name", "")}
    return None


def join(user: str, code: str, *, path: Path | None = None) -> str | None:
    """Вступает в группу по коду (из прежней выходит). Название группы или None, если кода нет."""
    store = path or COHORTS_PATH
    code = _norm(code)
    with _lock:
        data = _load(store)
        if code not in data:
            return None
        for c in data.values():
            if user in c.get("members", []):
                c["members"].remove(user)
        data[code].setdefault("members", []).append(user)
        _save(store, data)
        return data[code].get("name", "")


def leave(user: str, *, path: Path | None = None) -> bool:
    store = path or COHORTS_PATH
    with _lock:
        data = _load(store)
        changed = False
        for c in data.values():
            if user in c.get("members", []):
                c["members"].remove(user)
                changed = True
        if changed:
            _save(store, data)
    return changed


def cohort_stats(code: str, *, path: Path | None = None, sessions_dir: Path | None = None) -> dict[str, Any] | None:
    cohort = _load(path or COHORTS_PATH).get(_norm(code))
    if cohort is None:
        return None
    members = list(cohort.get("members", []))
    records = [r for r in load_sessions(sessions_dir) if r["user_id"] in members]

    rows = []
    for m in members:
        mine = sorted((r for r in records if r["user_id"] == m), key=lambda r: r["ts"])
        pcts = [r["percentage"] for r in mine]
        rows.append({
            "user": m,
            "sessions": len(mine),
            "avg": round(statistics.mean(pcts), 1) if pcts else None,
            "best": round(max(pcts), 1) if pcts else None,
            "trend": round(pcts[-1] - pcts[0], 1) if len(pcts) > 1 else None,
            "last": mine[-1]["ts"] if mine else None,
        })
    rows.sort(key=lambda r: (r["avg"] is None, -(r["avg"] or 0)))

    pcts_all = [r["percentage"] for r in records]
    dims: dict[str, list[float]] = {}
    for r in records:
        for k, v in (r["dimensions"] or {}).items():
            if isinstance(v, (int, float)) and v > 0:
                dims.setdefault(k, []).append(float(v))
    return {
        "code": _norm(code),
        "name": cohort.get("name", ""),
        "members": len(members),
        "active": sum(1 for r in rows if r["sessions"]),
        "sessions": len(records),
        "avg": round(statistics.mean(pcts_all), 1) if pcts_all else None,
        "distribution": {label: sum(1 for p in pcts_all if lo <= p < hi) for lo, hi, label in _BUCKETS},
        "dimensions": {DIMENSION_LABELS.get(k, k): round(statistics.mean(v), 1) for k, v in dims.items()},
        "rows": rows,
    }
