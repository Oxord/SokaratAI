from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

from .llm import extract_text, get_chat_model_for
from .prompts import load_prompt

log = logging.getLogger(__name__)

_MAX_TOPICS_OUT = 5
_MAX_RESOURCES_PER_TOPIC = 5
_MAX_TEXT_LEN = 120
_ALLOWED_TYPES = {"book", "docs", "course", "article", "video"}


def _parse_json_array(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _trim(value: Any, limit: int = _MAX_TEXT_LEN) -> str:
    s = str(value or "").strip()
    return s[:limit]


def _normalize_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return None


def _format_weak_topics(weak_topics: list[dict]) -> str:
    lines: list[str] = []
    for i, t in enumerate(weak_topics, 1):
        topic = str(t.get("topic", "")).strip()
        if not topic:
            continue
        why = str(t.get("why", "")).strip()
        category = str(t.get("category", "")).strip()
        skills = [str(s).strip() for s in (t.get("skills") or []) if str(s).strip()]
        parts = [f"{i}. {topic}"]
        if why:
            parts.append(f"   причина: {why}")
        if category:
            parts.append(f"   категория: {category}")
        if skills:
            parts.append(f"   связанные навыки: {', '.join(skills)}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines) if lines else "(нет данных)"


def generate_resources(
    role: str,
    level: str,
    weak_topics: list[dict],
    role_category: str = "tech",
) -> list[dict[str, Any]]:
    if not weak_topics:
        return []

    template = load_prompt("resource_generator")
    prompt = template.format(
        role=role,
        level=level,
        weak_topics=_format_weak_topics(weak_topics),
    )

    try:
        model = get_chat_model_for(role_category, temperature=0.4)
        response = model.invoke([HumanMessage(content=prompt)])
        raw = extract_text(response.content)
    except Exception:
        log.exception("resource generator: LLM call failed")
        return []

    items = _parse_json_array(raw)
    if not items:
        log.warning("resource generator: empty or unparseable JSON in response")
        return []

    out: list[dict[str, Any]] = []
    for item in items[:_MAX_TOPICS_OUT]:
        topic = _trim(item.get("topic"))
        if not topic:
            continue
        raw_resources = item.get("resources") or []
        if not isinstance(raw_resources, list):
            continue

        resources: list[dict[str, Any]] = []
        for r in raw_resources[:_MAX_RESOURCES_PER_TOPIC]:
            if not isinstance(r, dict):
                continue
            title = _trim(r.get("title"))
            if not title:
                continue
            r_type = str(r.get("type", "")).strip().lower()
            if r_type not in _ALLOWED_TYPES:
                r_type = "article"
            resources.append(
                {
                    "title": title,
                    "type": r_type,
                    "source": _trim(r.get("source")),
                    "url": _normalize_url(r.get("url")),
                    "search_query": _trim(r.get("search_query")),
                }
            )

        if not resources:
            continue

        out.append(
            {
                "topic": topic,
                "why_to_study": _trim(item.get("why_to_study"), limit=200),
                "resources": resources,
            }
        )
    return out
