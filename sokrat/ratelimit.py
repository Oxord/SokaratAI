"""Лимиты на запросы: защита бюджета LLM на пилоте.

Два вида лимитов, оба в памяти процесса (контейнер один; после перезапуска
счётчики обнуляются — для пилота этого достаточно):
  * скользящее окно «N запросов за T секунд» (против залипшей кнопки и скриптов);
  * суточная квота на создание интервью (главный расход LLM).

Значения настраиваются через env, 0 отключает соответствующий лимит.
"""
from __future__ import annotations

import os
import threading
import time


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, "").strip() or default))
    except ValueError:
        return default


# Запросов к «тяжёлым» эндпоинтам (ответ, резюме, план, практика, STT/TTS) в минуту.
HEAVY_PER_MINUTE = _env_int("SOKRAT_HEAVY_PER_MINUTE", 20)
# Новых интервью за сутки: для вошедшего и для гостя (гость считается по IP).
INTERVIEWS_PER_DAY_USER = _env_int("SOKRAT_INTERVIEWS_PER_DAY_USER", 10)
INTERVIEWS_PER_DAY_GUEST = _env_int("SOKRAT_INTERVIEWS_PER_DAY_GUEST", 3)

_MAX_KEYS = 10_000


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float) -> float:
        """Регистрирует обращение. 0 — можно; иначе секунд до освобождения места."""
        if limit <= 0:
            return 0.0
        now = time.time()
        with self._lock:
            if len(self._hits) > _MAX_KEYS:
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - window}
            recent = [t for t in self._hits.get(key, []) if t > now - window]
            if len(recent) >= limit:
                self._hits[key] = recent
                return max(1.0, recent[0] + window - now)
            recent.append(now)
            self._hits[key] = recent
        return 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def format_wait(seconds: float) -> str:
    if seconds >= 3600:
        return f"примерно через {int(seconds // 3600) + 1} ч"
    if seconds >= 60:
        return f"примерно через {int(seconds // 60) + 1} мин"
    return f"через {int(seconds)} с"
