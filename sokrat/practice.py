"""Практические задания после интервью: задача на код и мини-system design.

Решение пользователя НЕ исполняется: это LLM-ревью по критериям. Исполнение чужого
кода требует изолированной песочницы, это отдельная задача.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage

from .llm import extract_text, get_chat_model_for
from .prompts import load_prompt
from .vacancy_parser import _parse_json_object

log = logging.getLogger(__name__)

KINDS: dict[str, str] = {
    "code": "Задача на код",
    "design": "System design",
}
MIN_SOLUTION_CHARS = 20
MAX_SOLUTION_CHARS = 6000
_MAX_RUBRIC = 6


class PracticeError(RuntimeError):
    pass


@dataclass
class PracticeTask:
    kind: str
    title: str
    statement: str
    rubric: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)


@dataclass
class PracticeEvaluation:
    score: int
    summary: str
    criteria: list[dict[str, Any]] = field(default_factory=list)
    improvement: str = ""

    @property
    def met_count(self) -> int:
        return sum(1 for c in self.criteria if c.get("met"))


def _call_json(prompt: str, temperature: float) -> dict[str, Any]:
    try:
        model = get_chat_model_for("tech", temperature=temperature)
        response = model.invoke([HumanMessage(content=prompt)])
        data = _parse_json_object(extract_text(response.content))
    except Exception as exc:
        raise PracticeError(f"LLM не ответил ({exc})") from exc
    if not data:
        raise PracticeError("модель вернула невалидный JSON")
    return data


def _str_list(raw: Any, limit: int, max_len: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x).strip()[:max_len] for x in raw if str(x).strip()][:limit]


def generate_task(
    kind: str,
    role: str,
    level: str,
    skills: list[str],
    context: str = "",
) -> PracticeTask:
    if kind not in KINDS:
        raise PracticeError(f"неизвестный тип задания: {kind}")
    prompt = load_prompt("practice_task").format(
        kind_label=KINDS[kind],
        kind=kind,
        role=role,
        level=level,
        skills=", ".join(skills) if skills else "(не указаны)",
        context=context.strip() or "(нет)",
    )
    data = _call_json(prompt, temperature=0.7)
    statement = str(data.get("statement") or "").strip()
    rubric = _str_list(data.get("rubric"), _MAX_RUBRIC, 160)
    if len(statement) < 30 or not rubric:
        raise PracticeError("модель вернула неполное задание")
    return PracticeTask(
        kind=kind,
        title=str(data.get("title") or KINDS[kind]).strip()[:100],
        statement=statement[:1500],
        rubric=rubric,
        hints=_str_list(data.get("hints"), 3, 160),
    )


def evaluate_solution(task: PracticeTask, solution: str, level: str) -> PracticeEvaluation:
    text = (solution or "").strip()
    if len(text) < MIN_SOLUTION_CHARS:
        raise PracticeError("решение слишком короткое")
    text = text[:MAX_SOLUTION_CHARS]

    prompt = load_prompt("practice_review").format(
        kind_label=KINDS[task.kind],
        level=level,
        statement=task.statement,
        rubric="\n".join(f"{i}. {c}" for i, c in enumerate(task.rubric, 1)),
        solution=text,
    )
    data = _call_json(prompt, temperature=0.2)

    met_by_index: dict[int, dict[str, Any]] = {}
    for item in data.get("criteria") or []:
        if isinstance(item, dict):
            try:
                met_by_index[int(item.get("n"))] = item
            except (TypeError, ValueError):
                continue
    criteria = []
    for i, name in enumerate(task.rubric, 1):
        item = met_by_index.get(i, {})
        criteria.append(
            {
                "name": name,
                "met": bool(item.get("met")),
                "comment": str(item.get("comment") or "").strip()[:220],
            }
        )
    try:
        score = int(data.get("score"))
    except (TypeError, ValueError):
        score = round(10 * sum(c["met"] for c in criteria) / max(1, len(criteria)))
    return PracticeEvaluation(
        score=max(0, min(10, score)),
        summary=str(data.get("summary") or "").strip()[:500],
        criteria=criteria,
        improvement=str(data.get("improvement") or "").strip()[:1500],
    )


def format_task(task: PracticeTask) -> str:
    lines = [f"## 🧪 {KINDS[task.kind]}: {task.title}", "", task.statement, ""]
    if task.rubric:
        lines.append("**По каким критериям оценю:**")
        lines.extend(f"- {c}" for c in task.rubric)
    if task.hints:
        lines.append("\n_Подсказки:_ " + "; ".join(task.hints))
    where = (
        "Пришли решение одним сообщением (код в блоке ``` — по желанию)."
        if task.kind == "code"
        else "Опиши решение одним сообщением: компоненты, потоки данных, хранилища, узкие места."
    )
    lines.append(f"\n{where}")
    return "\n".join(lines)


def format_evaluation(ev: PracticeEvaluation) -> str:
    lines = [f"## 📝 Разбор решения — {ev.score}/10", ""]
    if ev.summary:
        lines += [ev.summary, ""]
    for c in ev.criteria:
        mark = "✅" if c["met"] else "❌"
        lines.append(f"- {mark} **{c['name']}**" + (f" — {c['comment']}" if c["comment"] else ""))
    if ev.improvement:
        lines += ["", "**Как усилить решение:**", ev.improvement]
    return "\n".join(lines)
