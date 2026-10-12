"""Извлечение структурированного контекста вакансии по ссылке или вставленному тексту.

Для hh.ru используем открытый JSON API (api.hh.ru/vacancies/<id>) — он даёт чистые
структурированные данные (стек, обязанности, компанию) без скрапинга HTML и без риска
попасть под anti-bot защиту. Для остальных источников (включая просто вставленный текст
вакансии, что обходит защиту LinkedIn/др. сайтов от скрапинга) — generic-фетч + LLM-экстракция.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import requests
from langchain_core.messages import HumanMessage

from .llm import extract_text, get_chat_model_for
from .net_safety import safe_get
from .prompts import load_prompt

log = logging.getLogger(__name__)

_FETCH_TIMEOUT = 10.0
_MAX_HTML_BYTES = 1_500_000
_MAX_TEXT_CHARS = 6000
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SokratAI/1.0"
    )
}

_HH_VACANCY_RE = re.compile(r"hh\.ru/vacancy/(\d+)", re.IGNORECASE)
_ALLOWED_LEVELS = {"Junior", "Middle", "Senior"}
_ALLOWED_TYPES = {"technical", "hr", "mixed"}


class VacancyParseError(RuntimeError):
    pass


@dataclass
class VacancyContext:
    title: str = ""
    company: str = ""
    level_guess: str | None = None
    interview_type_guess: str | None = None
    skills: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    company_description: str = ""
    source: str = ""  # "hh_api" | "html" | "raw_text"

    def is_empty(self) -> bool:
        return not self.title and not self.skills and not self.company_description

    def as_prompt_context(self) -> str:
        parts: list[str] = []
        if self.title:
            parts.append(f"Должность по вакансии: {self.title}")
        if self.company:
            parts.append(f"Компания: {self.company}")
        if self.company_description:
            parts.append(f"О компании/продукте: {self.company_description}")
        if self.skills:
            parts.append(f"Технологии/стек из вакансии: {', '.join(self.skills)}")
        if self.responsibilities:
            parts.append(
                "Обязанности по вакансии:\n"
                + "\n".join(f"- {r}" for r in self.responsibilities)
            )
        return "\n".join(parts)


def _looks_like_url(text: str) -> bool:
    return bool(re.match(r"^https?://", text.strip(), re.IGNORECASE))


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def _fetch_hh_api(vacancy_id: str) -> dict[str, Any] | None:
    try:
        resp = requests.get(
            f"https://api.hh.ru/vacancies/{vacancy_id}",
            headers=_HEADERS,
            timeout=_FETCH_TIMEOUT,
        )
    except requests.RequestException:
        log.exception("hh.ru API request failed")
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _vacancy_text_from_hh(data: dict[str, Any]) -> str:
    name = data.get("name") or ""
    employer = (data.get("employer") or {}).get("name") or ""
    description = _strip_html(data.get("description") or "")
    key_skills = ", ".join(
        s.get("name", "") for s in (data.get("key_skills") or []) if s.get("name")
    )
    experience = (data.get("experience") or {}).get("name") or ""
    snippet = data.get("snippet") or {}
    requirement = _strip_html(snippet.get("requirement") or "")
    responsibility = _strip_html(snippet.get("responsibility") or "")

    parts = [f"Вакансия: {name}", f"Компания: {employer}", f"Требуемый опыт: {experience}"]
    if key_skills:
        parts.append(f"Ключевые навыки: {key_skills}")
    if requirement:
        parts.append(f"Требования (кратко): {requirement}")
    if responsibility:
        parts.append(f"Обязанности (кратко): {responsibility}")
    parts.append(f"Описание: {description}")
    return "\n".join(p for p in parts if p.strip())


def _jobposting_nodes(obj: Any) -> list[dict[str, Any]]:
    """Все узлы schema.org/JobPosting внутри JSON-LD (включая @graph и списки)."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, list):
        for item in obj:
            found.extend(_jobposting_nodes(item))
    elif isinstance(obj, dict):
        node_type = obj.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if "JobPosting" in types:
            found.append(obj)
        if "@graph" in obj:
            found.extend(_jobposting_nodes(obj["@graph"]))
    return found


def _jobposting_text(html: str) -> str:
    """Текст вакансии из JSON-LD JobPosting (Хабр Карьера, SuperJob, карьерные сайты)."""
    for raw in re.findall(
        r"(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html
    ):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        for node in _jobposting_nodes(data):
            org = node.get("hiringOrganization")
            company = org.get("name") if isinstance(org, dict) else org
            skills = node.get("skills")
            if isinstance(skills, list):
                skills = ", ".join(str(s) for s in skills)
            experience = node.get("experienceRequirements")
            if isinstance(experience, dict):
                experience = experience.get("description") or experience.get("monthsOfExperience")
            parts = [
                f"Вакансия: {node.get('title') or node.get('name') or ''}",
                f"Компания: {company or ''}",
                f"Ключевые навыки: {skills}" if skills else "",
                f"Требуемый опыт: {experience}" if experience else "",
                f"Описание: {_strip_html(str(node.get('description') or ''))}",
            ]
            text = "\n".join(p for p in parts if p.strip() and not p.endswith(": "))
            if len(text) > 60:
                return text
    return ""


def _fetch_generic(url: str) -> tuple[str, str]:
    """Возвращает (текст, источник): "jsonld" для структурированной вакансии, иначе "html"."""
    try:
        resp = safe_get(url, headers=_HEADERS, timeout=_FETCH_TIMEOUT)
    except requests.RequestException as exc:
        raise VacancyParseError(f"не удалось загрузить страницу ({exc})") from exc
    if resp.status_code != 200:
        raise VacancyParseError(f"страница вернула HTTP {resp.status_code}")
    content = resp.content[:_MAX_HTML_BYTES]
    html = content.decode(resp.encoding or "utf-8", errors="ignore")
    structured = _jobposting_text(html)
    if structured:
        return structured, "jsonld"
    return _strip_html(html), "html"


def _fetch_source_text(url_or_text: str) -> tuple[str, str]:
    text = (url_or_text or "").strip()
    if not _looks_like_url(text):
        return text[:_MAX_TEXT_CHARS], "raw_text"

    hh_match = _HH_VACANCY_RE.search(text)
    if hh_match:
        data = _fetch_hh_api(hh_match.group(1))
        if data:
            return _vacancy_text_from_hh(data)[:_MAX_TEXT_CHARS], "hh_api"

    page_text, source = _fetch_generic(text)
    return page_text[:_MAX_TEXT_CHARS], source


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def extract_vacancy_context(url_or_text: str) -> VacancyContext:
    raw_text, source = _fetch_source_text(url_or_text)
    if not raw_text or len(raw_text) < 30:
        raise VacancyParseError(
            "не удалось извлечь текст вакансии — он слишком короткий или пустой"
        )

    template = load_prompt("vacancy_extractor")
    prompt = template.format(raw_text=raw_text)

    try:
        model = get_chat_model_for("general", temperature=0.0)
        response = model.invoke([HumanMessage(content=prompt)])
        raw = extract_text(response.content)
    except Exception as exc:
        raise VacancyParseError(f"LLM не смог обработать текст вакансии ({exc})") from exc

    data = _parse_json_object(raw)
    if not data:
        raise VacancyParseError("модель вернула невалидный JSON при разборе вакансии")

    level_guess = str(data.get("level_guess") or "").strip().title()
    interview_type_guess = str(data.get("interview_type_guess") or "").strip().lower()

    ctx = VacancyContext(
        title=str(data.get("title") or "").strip()[:80],
        company=str(data.get("company") or "").strip()[:80],
        level_guess=level_guess if level_guess in _ALLOWED_LEVELS else None,
        interview_type_guess=(
            interview_type_guess if interview_type_guess in _ALLOWED_TYPES else None
        ),
        skills=[str(s).strip()[:40] for s in (data.get("skills") or []) if str(s).strip()][:10],
        responsibilities=[
            str(s).strip()[:200] for s in (data.get("responsibilities") or []) if str(s).strip()
        ][:6],
        company_description=str(data.get("company_description") or "").strip()[:400],
        source=source,
    )
    if ctx.is_empty():
        raise VacancyParseError("не удалось распознать содержимое вакансии в этом тексте")
    return ctx
