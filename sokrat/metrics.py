"""Лёгкие метрики для пилота: счётчики запросов по эндпоинтам, ошибки, задержки.

Хранятся в памяти процесса; серверные ошибки (5xx) дополнительно дописываются в
DATA_DIR/errors.jsonl, чтобы после перезапуска было видно, где ломалось.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .config import DATA_DIR

ERRORS_PATH: Path = Path(os.getenv("ERRORS_PATH") or DATA_DIR / "errors.jsonl")
_RECENT = 50


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started = time.time()
        self._routes: dict[str, dict[str, Any]] = {}
        self._recent_errors: deque[dict[str, Any]] = deque(maxlen=_RECENT)

    def record(
        self, route: str, status: int, seconds: float, *, errors_path: Path | None = None
    ) -> None:
        with self._lock:
            row = self._routes.setdefault(
                route, {"count": 0, "s4xx": 0, "s5xx": 0, "total_s": 0.0, "max_s": 0.0}
            )
            row["count"] += 1
            row["total_s"] += seconds
            row["max_s"] = max(row["max_s"], seconds)
            if 400 <= status < 500:
                row["s4xx"] += 1
            if status >= 500:
                row["s5xx"] += 1
                entry = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "route": route,
                    "status": status,
                    "seconds": round(seconds, 2),
                }
                self._recent_errors.append(entry)
                self._append_error(entry, errors_path or ERRORS_PATH)

    @staticmethod
    def _append_error(entry: dict[str, Any], path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            routes = {
                name: {
                    "count": r["count"],
                    "4xx": r["s4xx"],
                    "5xx": r["s5xx"],
                    "avg_s": round(r["total_s"] / r["count"], 2) if r["count"] else 0.0,
                    "max_s": round(r["max_s"], 2),
                }
                for name, r in sorted(self._routes.items())
            }
            return {
                "uptime_s": int(time.time() - self.started),
                "routes": routes,
                "recent_errors": list(self._recent_errors),
            }


metrics = Metrics()
