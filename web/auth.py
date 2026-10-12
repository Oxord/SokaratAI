"""Подписанные cookie-сессии для веб-интерфейса (без внешних зависимостей)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from sokrat.config import DATA_DIR

COOKIE_NAME = "sokrat_session"
SESSION_DAYS = 14
_SECRET_PATH = Path(os.getenv("SOKRAT_SECRET_PATH") or DATA_DIR / ".secret")


def _load_secret() -> bytes:
    env = os.getenv("SOKRAT_SECRET", "").strip()
    if env:
        return env.encode("utf-8")
    try:
        return bytes.fromhex(_SECRET_PATH.read_text().strip())
    except (OSError, ValueError):
        pass
    secret = secrets.token_bytes(32)
    try:
        _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_PATH.write_text(secret.hex())
        os.chmod(_SECRET_PATH, 0o600)
    except OSError:
        pass  # без записи секрет живёт до перезапуска — сессии сбросятся
    return secret


_SECRET = _load_secret()


def _sign(payload: str) -> str:
    return hmac.new(_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_token(username: str, days: int = SESSION_DAYS) -> str:
    expires = int(time.time()) + days * 86400
    payload = base64.urlsafe_b64encode(f"{username}|{expires}".encode("utf-8")).decode("ascii")
    return f"{payload}.{_sign(payload)}"


def read_token(token: str | None) -> str | None:
    """Возвращает логин, если подпись верна и срок не истёк."""
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        username, expires = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8").rsplit("|", 1)
        if int(expires) < time.time():
            return None
    except (ValueError, UnicodeDecodeError):
        return None
    return username or None


class LoginThrottle:
    """Простая защита от перебора: N неудач за окно — блок на окно."""

    def __init__(self, limit: int = 6, window: int = 300) -> None:
        self.limit, self.window = limit, window
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = time.time() - self.window
        items = [t for t in self._failures.get(key, []) if t > cutoff]
        self._failures[key] = items
        return items

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.limit

    def fail(self, key: str) -> None:
        self._recent(key).append(time.time())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
