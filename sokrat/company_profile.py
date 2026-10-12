"""Профиль компании-работодателя: чем занимается, продукты, стек, культура.

Факты берём только из найденных источников (профиль работодателя на hh.ru, сайт
компании, текст вакансии). Если источников нет — профиль остаётся пустым: лучше ничего
не сказать, чем выдумать «факты» о компании.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests
from langchain_core.messages import HumanMessage

from .llm import extract_text, get_chat_model_for
from .prompts import load_prompt
from .vacancy_parser import (
    _FETCH_TIMEOUT,
    _HEADERS,
    VacancyContext,
    _parse_json_object,
    _strip_html,
)

log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 24 * 3600
_MAX_SOURCE_CHARS = 3500
_MAX_SITE_BYTES = 800_000
_MAX_LIST_ITEMS = 8
_MAX_ITEM_LEN = 120

_cache: dict[str, tuple[float, "CompanyProfile"]] = {}
_cache_lock = threading.Lock()


@dataclass
class CompanyProfile:
    name: str = ""
    industry: str = ""
    products: list[str] = field(default_factory=list)
    tech_stack: list[str] = field(default_factory=list)
    culture: str = ""
    initiatives: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.industry or self.products or self.tech_stack or self.culture)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "industry": self.industry,
            "products": self.products,
            "tech_stack": self.tech_stack,
            "culture": self.culture,
            "initiatives": self.initiatives,
            "sources": self.sources,
        }

    def as_prompt_context(self) -> str:
        if self.is_empty():
            return ""
        parts = [f"Профиль компании {self.name}:"]
        if self.industry:
            parts.append(f"- Сфера: {self.industry}")
        if self.products:
            parts.append(f"- Продукты и сервисы: {', '.join(self.products)}")
        if self.tech_stack:
            parts.append(f"- Технологии компании: {', '.join(self.tech_stack)}")
        if self.culture:
            parts.append(f"- Культура и подход к инженерии: {self.culture}")
        if self.initiatives:
            parts.append(f"- Текущие инициативы: {'; '.join(self.initiatives)}")
        return "\n".join(parts)


def _is_public_http_url(url: str) -> bool:
    """Защита от SSRF: ходим только на публичные http(s)-адреса."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not addr.is_global:
            return False
    return True


def _hh_get(path: str, **params: Any) -> Any | None:
    try:
        resp = requests.get(
            f"https://api.hh.ru{path}",
            params=params or None,
            headers=_HEADERS,
            timeout=_FETCH_TIMEOUT,
        )
    except requests.RequestException:
        log.warning("hh.ru request failed: %s", path, exc_info=True)
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _find_hh_employer(name: str) -> dict[str, Any] | None:
    data = _hh_get("/employers", text=name, per_page=5)
    items = (data or {}).get("items") or []
    if not items:
        return None
    wanted = name.strip().lower()
    for item in items:
        if str(item.get("name", "")).strip().lower() == wanted:
            return item
    # Иначе берём самого «живого» работодателя среди совпадений.
    return max(items, key=lambda i: i.get("open_vacancies") or 0)


def _fetch_site_text(url: str) -> str:
    if not _is_public_http_url(url):
        return ""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_FETCH_TIMEOUT)
    except requests.RequestException:
        return ""
    if resp.status_code != 200:
        return ""
    html = resp.content[:_MAX_SITE_BYTES].decode(resp.encoding or "utf-8", errors="ignore")
    return _strip_html(html)


def _collect_sources(company: str, vacancy: VacancyContext | None) -> tuple[str, list[str]]:
    """Собирает текст источников и их человекочитаемые названия."""
    blocks: list[str] = []
    sources: list[str] = []

    employer = _find_hh_employer(company)
    if employer and employer.get("id"):
        detail = _hh_get(f"/employers/{employer['id']}")
        if isinstance(detail, dict):
            description = _strip_html(detail.get("description") or "")
            industries = ", ".join(
                i.get("name", "") for i in (detail.get("industries") or []) if i.get("name")
            )
            if description or industries:
                head = f"Профиль работодателя на hh.ru ({detail.get('name') or company})"
                body = f"Отрасль: {industries}\n{description}" if industries else description
                blocks.append(f"[{head}]\n{body[:_MAX_SOURCE_CHARS]}")
                sources.append(f"hh.ru: профиль работодателя «{detail.get('name') or company}»")
            site_url = str(detail.get("site_url") or "").strip()
            if site_url:
                site_text = _fetch_site_text(site_url)
                if site_text:
                    blocks.append(f"[Сайт компании {site_url}]\n{site_text[:_MAX_SOURCE_CHARS]}")
                    sources.append(f"сайт компании: {site_url}")

    if vacancy and (vacancy.company_description or vacancy.skills):
        lines = []
        if vacancy.company_description:
            lines.append(f"О компании: {vacancy.company_description}")
        if vacancy.skills:
            lines.append(f"Стек из вакансии: {', '.join(vacancy.skills)}")
        if vacancy.responsibilities:
            lines.append("Задачи: " + "; ".join(vacancy.responsibilities))
        blocks.append("[Текст вакансии]\n" + "\n".join(lines))
        sources.append("текст вакансии")

    return "\n\n".join(blocks), sources


def _clean_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip()[:_MAX_ITEM_LEN]
        if text and text not in out:
            out.append(text)
    return out[:_MAX_LIST_ITEMS]


def build_company_profile(
    company: str, vacancy: VacancyContext | None = None
) -> CompanyProfile:
    """Собирает профиль компании. Никогда не бросает исключений — при сбое
    возвращает пустой профиль, чтобы интервью продолжилось без него."""
    name = (company or "").strip()
    if not name:
        return CompanyProfile()

    key = name.lower()
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    try:
        source_text, sources = _collect_sources(name, vacancy)
        if not source_text.strip():
            return CompanyProfile(name=name)

        prompt = load_prompt("company_profile").format(company=name, sources=source_text)
        model = get_chat_model_for("general", temperature=0.0)
        response = model.invoke([HumanMessage(content=prompt)])
        data = _parse_json_object(extract_text(response.content))
    except Exception:
        log.exception("company profile build failed (best-effort, ignored)")
        return CompanyProfile(name=name)

    profile = CompanyProfile(
        name=name,
        industry=str(data.get("industry") or "").strip()[:160],
        products=_clean_list(data.get("products")),
        tech_stack=_clean_list(data.get("tech_stack")),
        culture=str(data.get("culture") or "").strip()[:300],
        initiatives=_clean_list(data.get("initiatives")),
        sources=sources,
    )
    if profile.is_empty():
        profile.sources = []

    with _cache_lock:
        _cache[key] = (now, profile)
    return profile
