from __future__ import annotations

_MAX_MSG_LEN: int = 120


def format_error(prefix: str, exc: BaseException) -> str:
    """Compact, single-line error string for the session.errors log.

    Trims provider responses that may include URLs, headers or partial
    secrets, and removes newlines so the field stays grep-friendly.
    """
    raw = str(exc).replace("\n", " ").replace("\r", " ").strip()
    if len(raw) > _MAX_MSG_LEN:
        raw = raw[:_MAX_MSG_LEN].rstrip() + "…"
    head = f"{prefix}: {type(exc).__name__}"
    return f"{head}: {raw}" if raw else head
