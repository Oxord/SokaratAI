"""Сопоставление навыков из вакансии с резюме кандидата (без LLM, детерминированно)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .resume_parser import ResumeProfile

_ALIASES: dict[str, str] = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "golang": "go",
    "postgres": "postgresql",
    "psql": "postgresql",
    "k8s": "kubernetes",
    "mongo": "mongodb",
    "node": "nodejs",
    "node.js": "nodejs",
    "react.js": "react",
    "reactjs": "react",
    "vue.js": "vue",
    "vuejs": "vue",
    "ci cd": "ci/cd",
    "ml": "machine learning",
}


def _canon(skill: str) -> str:
    """Каноническое имя навыка: нижний регистр, схлопнутые пробелы, алиасы."""
    s = re.sub(r"[^\w+#./ -]+", " ", skill.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return _ALIASES.get(s, s)


def _surface_forms(canon: str) -> set[str]:
    """Все написания навыка: каноническое + алиасы, указывающие на него."""
    forms = {canon}
    forms.update(alias for alias, target in _ALIASES.items() if target == canon)
    return forms


def _mentioned_in(canon: str, text: str) -> bool:
    """Навык упомянут в тексте как отдельное слово/фраза (с учётом алиасов)."""
    if not canon or not text:
        return False
    haystack = text.lower()
    for form in _surface_forms(canon):
        pattern = r"(?<![\w+#])" + re.escape(form) + r"(?![\w+#])"
        if re.search(pattern, haystack):
            return True
    return False


@dataclass
class GapAnalysis:
    matched: list[str] = field(default_factory=list)
    weak_evidence: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.weak_evidence) + len(self.missing)

    @property
    def coverage_pct(self) -> float:
        """Доля требований вакансии, подтверждённых резюме (слабое подтверждение — 0.5)."""
        if self.total == 0:
            return 0.0
        score = len(self.matched) + 0.5 * len(self.weak_evidence)
        return round(100.0 * score / self.total, 1)

    @property
    def focus_skills(self) -> list[str]:
        """На чём строить интервью: сначала пробелы, затем слабо подтверждённое."""
        return self.missing + self.weak_evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "weak_evidence": self.weak_evidence,
            "missing": self.missing,
            "coverage_pct": self.coverage_pct,
        }

    def as_prompt_context(self) -> str:
        if self.total == 0:
            return ""
        lines = ["Сопоставление резюме кандидата с вакансией:"]
        if self.matched:
            lines.append(f"- Подтверждено опытом: {', '.join(self.matched)}")
        if self.weak_evidence:
            lines.append(
                f"- Указано в навыках, но без примеров опыта: {', '.join(self.weak_evidence)}"
            )
        if self.missing:
            lines.append(f"- Нет в резюме (пробел): {', '.join(self.missing)}")
        lines.append(
            "Уделяй больше вопросов пробелам и слабо подтверждённым навыкам; "
            "по подтверждённым проверяй глубину, опираясь на проекты из резюме."
        )
        return "\n".join(lines)


def analyze_gaps(vacancy_skills: list[str], resume: ResumeProfile) -> GapAnalysis:
    resume_skills = {_canon(s) for s in resume.skills if s.strip()}
    evidence = resume.evidence_text
    result = GapAnalysis()
    seen: set[str] = set()

    for skill in vacancy_skills:
        label = skill.strip()
        norm = _canon(label)
        if not norm or norm in seen:
            continue
        seen.add(norm)

        in_skills = norm in resume_skills
        in_evidence = _mentioned_in(norm, evidence)

        if in_evidence:
            result.matched.append(label)
        elif in_skills:
            result.weak_evidence.append(label)
        else:
            result.missing.append(label)
    return result
