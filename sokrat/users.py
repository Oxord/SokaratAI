"""Минимальное хранилище пользователей: логин + пароль (PBKDF2), файл data/users.json.

Подходит для пилота и демо. Для продакшена нужен полноценный провайдер
идентификации (OAuth/SSO) и хранение в БД.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from pathlib import Path

from .config import DATA_DIR

USERS_PATH: Path = Path(os.getenv("USERS_PATH") or DATA_DIR / "users.json")
_PBKDF2_ROUNDS = 200_000
_USERNAME_RE = re.compile(r"^[\w.@+-]{3,40}$")
_MIN_PASSWORD_LEN = 6
_lock = threading.Lock()


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS).hex()


def _load(path: Path) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, users: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def authenticate(
    username: str,
    password: str,
    *,
    allow_signup: bool = True,
    path: Path | None = None,
) -> bool:
    """True, если вход разрешён. Неизвестный логин регистрируется (если allow_signup)."""
    store = path or USERS_PATH
    name = (username or "").strip().lower()
    if not _USERNAME_RE.match(name) or len(password or "") < _MIN_PASSWORD_LEN:
        return False

    with _lock:
        users = _load(store)
        record = users.get(name)
        if record is None:
            if not allow_signup:
                return False
            salt = secrets.token_bytes(16)
            users[name] = {"salt": salt.hex(), "hash": _hash(password, salt)}
            _save(store, users)
            return True
        try:
            salt = bytes.fromhex(record["salt"])
            expected = record["hash"]
        except (KeyError, ValueError):
            return False
    return hmac.compare_digest(_hash(password, salt), expected)
