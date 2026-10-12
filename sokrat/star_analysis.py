"""Разбор поведенческого ответа по методу STAR (Situation, Task, Action, Result)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage

from .llm import extract_text, get_chat_model_for
from .prompts import load_prompt
from .vacancy_parser import _parse_json_object

log = logging.getLogger(__name__)

STAR_CATEGORIES = frozenset({"behavioral", "soft_skills", "motivation"})
STAR_PARTS: dict[str, str] = {
    "situation": "Ситуация",
    "task": "Задача",
    "action": "Действия",
    "result": "Результат",
}
_BEHAVIORAL_MARKERS = (
    "расскажи о случае",
    "расскажите о случае",
    "приведи пример",
    "приведите пример",
    "расскажи о ситуации",
    "расскажите о ситуации",
    "был ли у тебя опыт",
    "был ли у вас опыт",
    "как ты поступил",
    "как вы поступили",
)
_MAX_COMMENT = 220


@dataclass
class StarResult:
    parts: dict[str, dict[str, Any]] = field(default_factory=dict)
    tip: str = ""

    @property
    def present_count(self) -> int:
        return sum(1 for p in self.parts.values() if p.get("present"))

    @property
    def completeness(self) -> float:
        """Доля раскрытых элементов STAR, 0.0–1.0."""
        return self.present_count / len(STAR_PARTS) if self.parts else 0.0

    @property
    def missing(self) -> list[str]:
        return [k for k in STAR_PARTS if self.parts.get(k) and not self.parts[k]["present"]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "parts": self.parts,
            "completeness": round(self.completeness, 2),
            "missing": self.missing,
            "tip": self.tip,
        }


def is_behavioral(category: str | None, question_text: str) -> bool:
    if category in STAR_CATEGORIES:
        return True
    lowered = (question_text or "").lower()
    return any(marker in lowered for marker in _BEHAVIORAL_MARKERS)


def analyze_star(question: str, answer: str, level: str, role: str) -> StarResult | None:
    """Возвращает разбор по STAR или None, если модель не справилась (best-effort)."""
    prompt = load_prompt("star_analyzer").format(
        role=role, level=level, question=question, answer=answer
    )
    try:
        model = get_chat_model_for("general", temperature=0.2)
        response = model.invoke([HumanMessage(content=prompt)])
        data = _parse_json_object(extract_text(response.content))
    except Exception:
        log.exception("STAR analysis failed (best-effort, ignored)")
        return None

    parts: dict[str, dict[str, Any]] = {}
    for key in STAR_PARTS:
        raw = data.get(key)
        if not isinstance(raw, dict):
            return None
        parts[key] = {
            "present": bool(raw.get("present")),
            "comment": str(raw.get("comment") or "").strip()[:_MAX_COMMENT],
        }
    return StarResult(parts=parts, tip=str(data.get("tip") or "").strip()[:400])


def aggregate_star(scores: list[dict]) -> dict[str, Any] | None:
    """Сводка по всем поведенческим ответам интервью."""
    entries = [s["star"] for s in scores if isinstance(s.get("star"), dict)]
    if not entries:
        return None
    avg = sum(float(e.get("completeness") or 0.0) for e in entries) / len(entries)
    missing_counts: dict[str, int] = {}
    for e in entries:
        for key in e.get("missing") or []:
            missing_counts[key] = missing_counts.get(key, 0) + 1
    most_missed = max(missing_counts, key=missing_counts.get) if missing_counts else None
    return {
        "answers_count": len(entries),
        "avg_completeness_pct": round(avg * 100, 1),
        "most_missed": most_missed,
    }


def format_star_block(star: dict[str, Any]) -> str:
    """Компактный markdown-блок для сообщения с фидбэком."""
    lines = ["**🧩 Разбор по STAR**"]
    for key, label in STAR_PARTS.items():
        part = (star.get("parts") or {}).get(key) or {}
        mark = "✅" if part.get("present") else "❌"
        comment = part.get("comment")
        lines.append(f"- {mark} **{label}**" + (f" — {comment}" if comment else ""))
    if star.get("tip"):
        lines.append(f"\n💡 {star['tip']}")
    return "\n".join(lines)
