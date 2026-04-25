from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from langchain_core.messages import HumanMessage

from .llm import extract_text, get_chat_model
from .prompts import load_prompt

log = logging.getLogger(__name__)

_MAX_HINTS: int = 6
_MAX_KEYWORDS: int = 10
_EXISTING_TAIL: int = 30


def _normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip(" ?.!,:;\"'«»")


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


def _coerce_str_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        s = str(item).strip()
        if s:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def generate_questions(
    role: str,
    level: str,
    interview_type: str,
    count: int,
    existing: list[str],
    required_skills: list[str] | None = None,
) -> list[dict[str, Any]]:
    if count <= 0:
        return []

    template = load_prompt("question_generator")
    existing_tail = existing[-_EXISTING_TAIL:] if existing else []
    existing_text = (
        "\n".join(f"- {q}" for q in existing_tail)
        if existing_tail
        else "(вопросов пока нет)"
    )
    skills = [s for s in (required_skills or []) if s]
    skills_text = ", ".join(skills) if skills else "(не указаны — обычные вопросы по роли)"
    prompt = template.format(
        role=role,
        level=level,
        interview_type=interview_type,
        count=count,
        existing=existing_text,
        required_skills=skills_text,
    )

    try:
        model = get_chat_model(temperature=0.8)
        response = model.invoke([HumanMessage(content=prompt)])
        raw = extract_text(response.content)
    except Exception:
        log.exception("question generator: LLM call failed")
        return []

    items = _parse_json_array(raw)
    if not items:
        log.warning("question generator: empty or unparseable JSON in response")
        return []

    seen = {_normalize_question(q) for q in existing}
    out: list[dict[str, Any]] = []
    for item in items:
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        normalized = _normalize_question(question)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(
            {
                "id": f"gen_{uuid.uuid4().hex[:10]}",
                "question": question,
                "hints": _coerce_str_list(item.get("hints"), _MAX_HINTS),
                "ideal_keywords": _coerce_str_list(
                    item.get("ideal_keywords"), _MAX_KEYWORDS
                ),
            }
        )
        if len(out) >= count:
            break
    return out
