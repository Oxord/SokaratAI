"""Чистая логика интервью: границы длины, остановка, банк вопросов, подбор, слабые темы.

Не зависит от UI (Chainlit / веб). Используется и app.py, и веб-движком (sokrat/engine.py).
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
from pathlib import Path

from .question_generator import generate_questions
from .score_bands import (
    EXCELLENT,
    GOOD,
    NEEDS_WORK,
    READINESS_MIN_ANSWERS,
    SKIPPED,
    STABLE_WEAK_MAX,
    band,
    is_weak,
)

log = logging.getLogger(__name__)

QUESTIONS_PATH = Path(os.getenv("QUESTIONS_PATH", "data/questions.json"))

BANK_REFRESH_PER_SESSION = 2

# Adaptive interview length: base bounds per level + extra capacity per required skill.
# A run will ask at least `min_q` questions and at most `max_q`; in between, it stops
# once competence signal is clear (see should_stop_interview).
_LEVEL_BOUNDS: dict[str, tuple[int, int]] = {
    "Junior": (6, 10),
    "Middle": (8, 14),
    "Senior": (10, 18),
}

# Фиксированные границы для явно выбранных режимов (quick / full).
# Для "medium" границы не заданы — используется адаптивная логика по уровню/скиллам.
_MODE_BOUNDS: dict[str, tuple[int, int]] = {
    "quick": (4, 6),
    "full":  (14, 20),
}

_PER_SKILL_MIN = 1

_PER_SKILL_MAX = 2

_HARD_MAX = 20

# Each required skill must collect at least this many scored answers before
# we consider the skill "covered" for early stopping.
_MIN_HITS_PER_SKILL = 2

# When no skills are specified, stop early after this many consecutive answers
# in the same band (all strong or all weak) — the signal is already clear.
_STABLE_RUN = 3

def compute_bounds(level: str | None, num_skills: int, mode: str | None = None) -> tuple[int, int]:
    if mode in _MODE_BOUNDS:
        min_q, max_q = _MODE_BOUNDS[mode]
        return min_q, min(_HARD_MAX, max_q)
    base_min, base_max = _LEVEL_BOUNDS.get(level or "", _LEVEL_BOUNDS["Middle"])
    extra = max(0, num_skills)
    min_q = base_min + extra * _PER_SKILL_MIN
    max_q = min(_HARD_MAX, base_max + extra * _PER_SKILL_MAX)
    if min_q > max_q:
        min_q = max_q
    return min_q, max_q

def should_stop_interview(
    asked: int,
    min_q: int,
    max_q: int,
    scores: list[dict],
    required_skills: list[str],
) -> bool:
    if asked >= max_q:
        return True
    # Раньше READINESS_MIN_ANSWERS вопросов не останавливаемся, иначе сильный
    # кандидат не наберёт ответов на индекс готовности.
    if asked < max(min_q, min(READINESS_MIN_ANSWERS, max_q)):
        return False

    if required_skills:
        # Stop only when every skill has been probed enough times with a real answer.
        hits: dict[str, int] = {s.lower(): 0 for s in required_skills}
        for s in scores:
            if s.get("score", 0) <= 0:
                continue
            for t in s.get("skills_touched") or []:
                key = str(t).lower()
                if key in hits:
                    hits[key] += 1
        return all(count >= _MIN_HITS_PER_SKILL for count in hits.values())

    # No explicit skills → stop early if signal is already consistent.
    nonzero = [s["score"] for s in scores if s.get("score", 0) > 0]
    if len(nonzero) < _STABLE_RUN:
        return False
    tail = nonzero[-_STABLE_RUN:]
    return all(band(v) == EXCELLENT for v in tail) or all(v <= STABLE_WEAK_MAX for v in tail)

_BANK_LOCK = threading.Lock()

ROLES = ["Python Developer", "Frontend Developer", "Product Manager"]

LEVELS = ["Junior", "Middle", "Senior"]

LEVEL_LABELS: dict[str, dict[str, str]] = {
    "tech":    {"Junior": "Junior", "Middle": "Middle", "Senior": "Senior"},
    "general": {"Junior": "До 1 года", "Middle": "1–3 года", "Senior": "3+ года"},
}

INTERVIEW_TYPES = {
    "technical": "Техническое (стек, фреймворки, инструменты)",
    "hr": "HR / Поведенческое (STAR, мотивация, конфликты)",
    "mixed": "Смешанное (технические + HR вопросы)",
}

INTERVIEW_MODES = {
    "quick":  ("Быстрое",  "4–6 вопросов, поверхностный прогон"),
    "medium": ("Среднее",  "адаптивно под уровень и скиллы (6–18)"),
    "full":   ("Полное",   "14–20 вопросов, глубокий разбор"),
}

FALLBACK_QUESTIONS = [
    {
        "id": "fallback_1",
        "question": "Расскажи о себе и своём профессиональном опыте.",
        "hints": ["опыт", "проекты", "стек"],
        "ideal_keywords": ["опыт", "работал", "проект", "стек", "технологии", "команда"],
    },
    {
        "id": "fallback_2",
        "question": "Назови свои сильные и слабые стороны как специалиста.",
        "hints": ["честность", "конкретность", "рост"],
        "ideal_keywords": ["сильный", "слабый", "работаю над", "улучшаю", "навык"],
    },
    {
        "id": "fallback_3",
        "question": "Почему ты хочешь работать в нашей компании?",
        "hints": ["исследование компании", "ценности", "мотивация"],
        "ideal_keywords": ["компания", "ценности", "продукт", "команда", "рост", "интересно"],
    },
    {
        "id": "fallback_4",
        "question": "Расскажи о самом сложном проекте в твоей карьере.",
        "hints": ["сложность", "решение", "результат"],
        "ideal_keywords": ["проект", "сложно", "решил", "научился", "результат", "команда"],
    },
    {
        "id": "fallback_5",
        "question": "Как ты справляешься с трудными коллегами или конфликтами в команде?",
        "hints": ["коммуникация", "эмпатия", "решение"],
        "ideal_keywords": ["коммуникация", "конфликт", "понял", "решили", "команда", "договорились"],
    },
    {
        "id": "fallback_6",
        "question": "Где ты видишь себя через 3 года?",
        "hints": ["карьерные цели", "рост", "конкретность"],
        "ideal_keywords": ["цель", "рост", "навык", "компания", "вклад", "развитие"],
    },
    {
        "id": "fallback_7",
        "question": "Есть ли у тебя вопросы к нам?",
        "hints": ["команда", "процессы", "рост", "продукт"],
        "ideal_keywords": ["вопрос", "команда", "процесс", "продукт", "рост", "интересно"],
    },
]

def load_questions_db() -> dict:
    try:
        with open(QUESTIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise

def _append_questions_to_bank(
    role: str, level: str, interview_type: str, items: list[dict]
) -> None:
    if not items:
        return
    with _BANK_LOCK:
        db = load_questions_db()
        roles = db.setdefault("roles", {})
        role_dict = roles.setdefault(role, {})
        level_dict = role_dict.setdefault(level, {})
        bucket = level_dict.setdefault(interview_type, [])
        bucket.extend(items)
        _atomic_write_json(QUESTIONS_PATH, db)

def get_questions(db: dict, role: str, level: str, interview_type: str, count: int) -> list[dict]:
    try:
        pool = db["roles"][role][level][interview_type]
    except KeyError:
        pool = []

    if len(pool) < count:
        pool = pool + FALLBACK_QUESTIONS
        pool = pool[:count]

    selected = random.sample(pool, min(count, len(pool)))
    return selected

def _bank_pool(db: dict, role: str, level: str, interview_type: str) -> list[dict]:
    try:
        return list(db["roles"][role][level][interview_type])
    except KeyError:
        return []

def get_personalized_questions(
    db: dict,
    role: str,
    level: str,
    interview_type: str,
    required_skills: list[str],
    is_custom_role: bool,
    count: int,
    role_category: str = "tech",
    job_context: str = "",
) -> list[dict]:
    """Build a pool of `count` questions for a session.

    No skills + standard role → existing fast path (bank + fallback).
    Custom role or skills specified → mix of bank picks and skill-targeted
    LLM questions, generated synchronously so the interview can start.
    Skill-specific questions are NOT persisted to the bank.

    The interview may consume fewer than `count` questions if the dynamic
    stop condition fires earlier; we still pre-build the full pool so we
    never run out mid-interview.
    """
    if not required_skills and not is_custom_role:
        return get_questions(db, role, level, interview_type, count)

    bank_pool = _bank_pool(db, role, level, interview_type)

    if required_skills:
        bank_take = min(len(bank_pool), max(1, count // 4))
    else:
        bank_take = min(len(bank_pool), count)

    bank_picked = random.sample(bank_pool, bank_take) if bank_take else []
    need = count - len(bank_picked)

    generated: list[dict] = []
    if need > 0:
        existing_texts = [q["question"] for q in bank_pool if isinstance(q, dict) and q.get("question")]
        existing_texts.extend(q["question"] for q in bank_picked if q.get("question"))
        try:
            generated = generate_questions(
                role,
                level,
                interview_type,
                need,
                existing_texts,
                required_skills,
                role_category=role_category,
                job_context=job_context,
            )
        except Exception:
            log.exception("personalized question generation failed")
            generated = []

    selected = bank_picked + generated
    if len(selected) < count:
        selected = selected + FALLBACK_QUESTIONS[: count - len(selected)]
    random.shuffle(selected)
    return selected[:count]

_MAX_WEAK_TOPICS_INPUT = 7

_QUESTION_SNIPPET_LEN = 80

def _question_snippet(text: str, limit: int = _QUESTION_SNIPPET_LEN) -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0]
    return (cut or s[:limit]).rstrip(",.!?;:") + "…"

def _extract_weak_topics(
    scores: list[dict],
    questions: list[dict],
    skills_coverage: list[dict],
) -> list[dict]:
    """Build a unified list of weak topics for the resource generator.

    Combines three signals: weak skills (avg<GOOD_MIN), weak answers (score 1-5),
    and skipped/dont_know answers (score==0). Deduped by topic name.
    """
    by_key: dict[str, dict] = {}

    for entry in skills_coverage or []:
        if entry.get("questions_count", 0) <= 0:
            continue
        avg = entry.get("avg_score") or 0.0
        if not is_weak(avg):
            continue
        skill = str(entry.get("skill", "")).strip()
        if not skill:
            continue
        by_key[skill.lower()] = {
            "topic": skill,
            "why": f"средний балл {avg}/10 по {entry['questions_count']} вопросам",
            "category": "",
            "skills": [skill],
        }

    for i, s in enumerate(scores or []):
        if i >= len(questions):
            break
        score_val = s.get("score", 0)
        intent = s.get("intent", "answer")
        is_weak_answer = is_weak(score_val)
        is_skipped = score_val == 0 and intent in ("skip", "dont_know")
        if not (is_weak_answer or is_skipped):
            continue

        question_text = (questions[i] or {}).get("question", "")
        topic = _question_snippet(question_text)
        if not topic:
            continue
        key = topic.lower()
        if key in by_key:
            continue

        if is_skipped:
            why = (
                "вопрос пропущен"
                if intent == "skip"
                else "пользователь ответил «не знаю»"
            )
        else:
            weaknesses = [str(w).strip() for w in (s.get("weaknesses") or []) if str(w).strip()]
            why = "; ".join(weaknesses) if weaknesses else f"слабый ответ ({score_val}/10)"

        by_key[key] = {
            "topic": topic,
            "why": why,
            "category": str(s.get("category", "") or "").strip(),
            "skills": [str(t).strip() for t in (s.get("skills_touched") or []) if str(t).strip()],
        }

    return list(by_key.values())[:_MAX_WEAK_TOPICS_INPUT]

_MAX_SKILLS = 10

_MAX_SKILL_LEN = 40

_MAX_ROLE_LEN = 80

def _parse_skills(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for chunk in (raw or "").replace(";", ",").split(","):
        s = chunk.strip()
        if not s:
            continue
        s = s[:_MAX_SKILL_LEN]
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= _MAX_SKILLS:
            break
    return out

TYPE_LABEL = {"technical": "Техническое", "hr": "HR / Поведенческое", "mixed": "Смешанное"}

TYPE_EMOJI = {"technical": "🛠️", "hr": "🤝", "mixed": "🎯"}

def verdict_for_score(score: int) -> tuple[str, str, str]:
    """Returns (verdict_key, emoji, label) based on numeric score."""
    key = band(score)
    if key == SKIPPED:
        return key, "⏭️", "Пропущено"
    if key == EXCELLENT:
        return key, "✅", "Отлично"
    if key == GOOD:
        return key, "👍", "Хорошо"
    return NEEDS_WORK, "⚠️", "Можно лучше"

def _pace_score(seconds: float) -> int:
    """Детерминированная оценка темпа ответа по бакетам (0–10)."""
    if seconds < 15:
        return 5
    if seconds < 60:
        return 9
    if seconds < 120:
        return 10
    if seconds < 240:
        return 8
    if seconds < 420:
        return 6
    if seconds < 600:
        return 4
    return 2
