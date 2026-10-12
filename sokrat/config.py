import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PACKAGE_DIR: Path = Path(__file__).resolve().parent
PROMPTS_DIR: Path = PACKAGE_DIR / "prompts"
# Данные лежат в корне проекта (как у app.py и в .gitignore), а не внутри пакета.
DATA_DIR: Path = PACKAGE_DIR.parent / "data"
SESSIONS_DIR: Path = Path(os.getenv("SESSION_DIR") or DATA_DIR / "sessions")


@dataclass(frozen=True)
class Settings:
    # Активный LLM-провайдер: "deepseek" (прямой API) или "openrouter".
    LLM_PROVIDER: str
    LLM_API_KEY: str
    LLM_BASE_URL: str
    MODEL_TECH: str
    MODEL_GENERAL: str
    # Заголовки атрибуции нужны только OpenRouter.
    OPENROUTER_REFERER: str | None
    INTERVIEW_QUESTIONS_COUNT: int


_DEFAULT_QUESTIONS_COUNT: int = 7
_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# deepseek-chat поддерживает JSON-режим и temperature; deepseek-reasoner — нет,
# поэтому по умолчанию используем именно chat.
_DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _safe_int(raw: str | None, default: int) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _resolve_provider() -> str:
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit in ("deepseek", "openrouter"):
        return explicit
    # Без явного выбора: DeepSeek, если задан его ключ; иначе прежний OpenRouter.
    return "deepseek" if os.getenv("DEEPSEEK_API_KEY", "").strip() else "openrouter"


def _load_settings() -> Settings:
    provider = _resolve_provider()
    questions = _safe_int(os.getenv("INTERVIEW_QUESTIONS_COUNT"), _DEFAULT_QUESTIONS_COUNT)

    if provider == "deepseek":
        return Settings(
            LLM_PROVIDER=provider,
            LLM_API_KEY=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            LLM_BASE_URL=os.getenv("DEEPSEEK_BASE_URL", "").strip() or _DEEPSEEK_BASE_URL,
            MODEL_TECH=os.getenv("DEEPSEEK_MODEL_TECH", "").strip() or _DEEPSEEK_DEFAULT_MODEL,
            MODEL_GENERAL=os.getenv("DEEPSEEK_MODEL_GENERAL", "").strip()
            or _DEEPSEEK_DEFAULT_MODEL,
            OPENROUTER_REFERER=None,
            INTERVIEW_QUESTIONS_COUNT=questions,
        )

    legacy_model_name = os.getenv("MODEL_NAME")
    return Settings(
        LLM_PROVIDER=provider,
        LLM_API_KEY=os.getenv("OPENROUTER_API_KEY", "").strip(),
        LLM_BASE_URL=os.getenv("OPENROUTER_BASE_URL", "").strip() or _OPENROUTER_BASE_URL,
        MODEL_TECH=os.getenv("MODEL_TECH")
        or legacy_model_name
        or "anthropic/claude-sonnet-4-5",
        MODEL_GENERAL=os.getenv("MODEL_GENERAL")
        or legacy_model_name
        or "deepseek/deepseek-chat",
        OPENROUTER_REFERER=os.getenv("OPENROUTER_REFERER"),
        INTERVIEW_QUESTIONS_COUNT=questions,
    )


settings: Settings = _load_settings()
