"""Разбор резюме кандидата в структурированный профиль.

Персональные данные: перед отправкой в LLM из текста вырезаются email, телефоны и
ссылки на соцсети, а в профиль контакты не попадают вовсе. Исходный текст резюме не
хранится — сохраняется только структурированный профиль.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from .llm import extract_text, get_chat_model_for
from .prompts import load_prompt
from .vacancy_parser import _parse_json_object

log = logging.getLogger(__name__)

_MAX_RESUME_CHARS = 8000
_MIN_RESUME_CHARS = 80
_MAX_FILE_BYTES = 5 * 1024 * 1024
_ALLOWED_LEVELS = {"Junior", "Middle", "Senior"}
_SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
_SOCIAL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:linkedin\.com|t\.me|vk\.com|facebook\.com|instagram\.com)"
    r"/\S*",
    re.IGNORECASE,
)


class ResumeParseError(RuntimeError):
    pass


@dataclass
class ResumeProfile:
    headline: str = ""
    level_guess: str | None = None
    years_experience: float | None = None
    skills: list[str] = field(default_factory=list)
    experience: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    achievements: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.skills or self.experience or self.projects or self.headline)

    @property
    def evidence_text(self) -> str:
        """Текст, в котором навык может упоминаться как «подтверждённый опытом»."""
        return "\n".join(self.experience + self.projects + self.achievements + [self.headline])

    def to_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "level_guess": self.level_guess,
            "years_experience": self.years_experience,
            "skills": self.skills,
            "experience": self.experience,
            "projects": self.projects,
            "achievements": self.achievements,
        }

    def as_prompt_context(self) -> str:
        parts: list[str] = ["Резюме кандидата:"]
        if self.headline:
            parts.append(f"- Кто он: {self.headline}")
        if self.years_experience is not None:
            parts.append(f"- Опыт: ~{self.years_experience:g} лет")
        if self.skills:
            parts.append(f"- Заявленные навыки: {', '.join(self.skills)}")
        if self.experience:
            parts.append("- Опыт работы:\n" + "\n".join(f"  • {e}" for e in self.experience))
        if self.projects:
            parts.append("- Проекты:\n" + "\n".join(f"  • {p}" for p in self.projects))
        if self.achievements:
            parts.append("- Достижения:\n" + "\n".join(f"  • {a}" for a in self.achievements))
        return "\n".join(parts) if len(parts) > 1 else ""


def scrub_pii(text: str) -> str:
    text = _EMAIL_RE.sub(" ", text)
    text = _SOCIAL_RE.sub(" ", text)
    text = _PHONE_RE.sub(" ", text)
    return text


def extract_resume_text(path: str | Path) -> str:
    """Достаёт текст из .txt/.md/.pdf/.docx. Бросает ResumeParseError с понятным текстом."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise ResumeParseError(
            f"формат «{suffix or 'без расширения'}» не поддерживается — "
            "загрузи PDF, DOCX или TXT"
        )
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        raise ResumeParseError(f"не удалось открыть файл ({exc})") from exc
    if size > _MAX_FILE_BYTES:
        raise ResumeParseError("файл слишком большой (максимум 5 МБ)")

    try:
        if suffix in (".txt", ".md"):
            return file_path.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise ResumeParseError(
                    "для PDF нужен пакет pypdf (pip install pypdf)"
                ) from exc
            reader = PdfReader(str(file_path))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        try:
            from docx import Document
        except ImportError as exc:
            raise ResumeParseError(
                "для DOCX нужен пакет python-docx (pip install python-docx)"
            ) from exc
        doc = Document(str(file_path))
        return "\n".join(p.text for p in doc.paragraphs)
    except ResumeParseError:
        raise
    except Exception as exc:
        raise ResumeParseError(f"не удалось прочитать файл ({exc})") from exc


def _clean_list(raw: Any, max_items: int, max_len: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip()[:max_len]
        if text and text not in out:
            out.append(text)
    return out[:max_items]


def parse_resume(text: str) -> ResumeProfile:
    cleaned = scrub_pii(text or "").strip()
    if len(cleaned) < _MIN_RESUME_CHARS:
        raise ResumeParseError("в файле слишком мало текста — похоже, это не резюме")
    cleaned = cleaned[:_MAX_RESUME_CHARS]

    prompt = load_prompt("resume_extractor").format(resume_text=cleaned)
    try:
        model = get_chat_model_for("general", temperature=0.0)
        response = model.invoke([HumanMessage(content=prompt)])
        data = _parse_json_object(extract_text(response.content))
    except Exception as exc:
        raise ResumeParseError(f"LLM не смог обработать резюме ({exc})") from exc
    if not data:
        raise ResumeParseError("модель вернула невалидный JSON при разборе резюме")

    level_guess = str(data.get("level_guess") or "").strip().title()
    years: float | None
    try:
        years = float(data["years_experience"]) if data.get("years_experience") is not None else None
    except (TypeError, ValueError):
        years = None
    if years is not None and not (0 <= years <= 60):
        years = None

    profile = ResumeProfile(
        headline=str(data.get("headline") or "").strip()[:160],
        level_guess=level_guess if level_guess in _ALLOWED_LEVELS else None,
        years_experience=years,
        skills=_clean_list(data.get("skills"), 25, 40),
        experience=_clean_list(data.get("experience"), 8, 220),
        projects=_clean_list(data.get("projects"), 6, 220),
        achievements=_clean_list(data.get("achievements"), 6, 200),
    )
    if profile.is_empty():
        raise ResumeParseError("не удалось распознать содержимое резюме")
    return profile
