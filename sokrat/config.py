import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PACKAGE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = PACKAGE_DIR / "data"
SESSIONS_DIR: Path = DATA_DIR / "sessions"
QUESTIONS_PATH: Path = DATA_DIR / "questions.json"
PROMPTS_DIR: Path = PACKAGE_DIR / "prompts"


@dataclass(frozen=True)
class Settings:
    OPENROUTER_API_KEY: str
    OPENROUTER_BASE_URL: str
    OPENROUTER_REFERER: str | None
    MODEL_NAME: str
    INTERVIEW_QUESTIONS_COUNT: int


_DEFAULT_QUESTIONS_COUNT: int = 7


def _safe_int(raw: str | None, default: int) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _load_settings() -> Settings:
    return Settings(
        OPENROUTER_API_KEY=os.getenv("OPENROUTER_API_KEY", ""),
        OPENROUTER_BASE_URL=os.getenv(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
        OPENROUTER_REFERER=os.getenv("OPENROUTER_REFERER"),
        MODEL_NAME=os.getenv("MODEL_NAME", "anthropic/claude-sonnet-4"),
        INTERVIEW_QUESTIONS_COUNT=_safe_int(
            os.getenv("INTERVIEW_QUESTIONS_COUNT"), _DEFAULT_QUESTIONS_COUNT
        ),
    )


settings: Settings = _load_settings()
