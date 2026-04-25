from __future__ import annotations

from functools import lru_cache

from ..config import PROMPTS_DIR


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
