import asyncio
import json
import logging
import os
import random
import threading
import time
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import quote_plus

import chainlit as cl
from dotenv import load_dotenv

from sokrat.analyzer import AnswerEvaluation, DIMENSION_LABELS
from sokrat.graph import (
    PHASE_ANALYZE,
    PHASE_FINALIZE,
    PHASE_NEXT_QUESTION,
    PHASE_PERSIST,
    PHASE_START,
    build_graph,
)
from sokrat.question_generator import generate_questions
from sokrat.resource_generator import generate_resources
from sokrat.role_classifier import classify_role
from sokrat.stt import SaluteSpeechError, get_stt_client
from sokrat.summary_builder import compute_summary

_GRAPH = build_graph()

log = logging.getLogger(__name__)

load_dotenv()

SESSION_DIR = Path(os.getenv("SESSION_DIR", "data/sessions"))
QUESTIONS_PATH = Path(os.getenv("QUESTIONS_PATH", "data/questions.json"))
BANK_REFRESH_PER_SESSION = 2

# Должно совпадать с [features.audio].sample_rate в .chainlit/config.toml.
AUDIO_SAMPLE_RATE = 16000

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
    if asked < min_q:
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
    return all(v >= 8 for v in tail) or all(v <= 3 for v in tail)

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


# ── Data loading ──────────────────────────────────────────────────────────────

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


async def _enrich_bank_async(
    role: str, level: str, interview_type: str, existing_texts: list[str]
) -> None:
    try:
        new_items = await asyncio.to_thread(
            generate_questions,
            role,
            level,
            interview_type,
            BANK_REFRESH_PER_SESSION,
            existing_texts,
        )
        if new_items:
            await asyncio.to_thread(
                _append_questions_to_bank, role, level, interview_type, new_items
            )
            log.info(
                "bank enriched: role=%s level=%s type=%s added=%d",
                role, level, interview_type, len(new_items),
            )
    except Exception:
        log.exception("bank enrichment failed (best-effort, ignored)")


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
            )
        except Exception:
            log.exception("personalized question generation failed")
            generated = []

    selected = bank_picked + generated
    if len(selected) < count:
        selected = selected + FALLBACK_QUESTIONS[: count - len(selected)]
    random.shuffle(selected)
    return selected[:count]


# ── Resources / weak topics ──────────────────────────────────────────────────

# Жёсткий потолок на тиканье таймера, чтобы фоновая задача не жила вечно,
# если пользователь ушёл со страницы или забыл про вкладку.
TIMER_HARD_CAP_SEC = 30 * 60


_RESOURCE_TYPE_EMOJI = {
    "book": "📘",
    "docs": "📚",
    "course": "🎓",
    "article": "📝",
    "video": "🎬",
}
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

    Combines three signals: weak skills (avg<6), weak answers (score 1-6),
    and skipped/dont_know answers (score==0). Deduped by topic name.
    """
    by_key: dict[str, dict] = {}

    for entry in skills_coverage or []:
        if entry.get("questions_count", 0) <= 0:
            continue
        avg = entry.get("avg_score") or 0.0
        if not (0 < avg < 6):
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
        is_weak_answer = 0 < score_val <= 6
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


def _format_resources_section(resources: list[dict]) -> str:
    if not resources:
        return ""

    blocks: list[str] = ["### Что почитать и изучить", ""]
    for entry in resources:
        topic = str(entry.get("topic", "")).strip()
        if not topic:
            continue
        why = str(entry.get("why_to_study", "")).strip()
        header = f"**{topic}**" + (f" — {why}" if why else "")
        blocks.append(header)

        for r in entry.get("resources") or []:
            title = str(r.get("title", "")).strip()
            if not title:
                continue
            emoji = _RESOURCE_TYPE_EMOJI.get(r.get("type", ""), "📝")
            url = r.get("url")
            if not url:
                query = str(r.get("search_query", "")).strip() or title
                url = f"https://www.google.com/search?q={quote_plus(query)}"
            source = str(r.get("source", "")).strip()
            tail = f" — {source}" if source else ""
            blocks.append(f"- {emoji} [{title}]({url}){tail}")

        blocks.append("")

    return "\n".join(blocks) + "\n"


def _build_radar_figure(dimensions: dict[str, float]):
    """Plotly radar chart over interview dimensions. Lazy-imports plotly so the
    rest of the app keeps working even if the dependency is not installed yet.
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None

    labels = [DIMENSION_LABELS[k] for k in DIMENSION_LABELS]
    values = [dimensions.get(k, 0.0) for k in DIMENSION_LABELS]
    # Замыкаем контур радара (первая точка == последняя).
    labels_closed = labels + [labels[0]]
    values_closed = values + [values[0]]

    fig = go.Figure(
        data=[
            go.Scatterpolar(
                r=values_closed,
                theta=labels_closed,
                fill="toself",
                name="Твой результат",
                line=dict(color="#5B8DEF", width=2),
                fillcolor="rgba(91,141,239,0.25)",
            )
        ]
    )
    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 10], tickvals=[2, 4, 6, 8, 10]),
        ),
        showlegend=False,
        margin=dict(l=40, r=40, t=40, b=40),
        title="Профиль интервью (0–10)",
    )
    return fig


# ── Button factories ──────────────────────────────────────────────────────────

_ROLE_EMOJI: dict[str, str] = {
    "Python Developer": "🐍",
    "Frontend Developer": "💻",
    "Product Manager": "🧭",
}

_TYPE_SHORT_LABELS: dict[str, str] = {
    "technical": "🧩 Техническое",
    "hr": "🤝 HR",
    "mixed": "🎯 Смешанное",
}


def make_role_actions() -> list[cl.Action]:
    actions = [
        cl.Action(
            name="select_role",
            label=f"{_ROLE_EMOJI.get(role, '👤')} {role}",
            payload={"value": role},
        )
        for role in ROLES
    ]
    actions.append(
        cl.Action(
            name="select_role_custom",
            label="✏️ Другая роль…",
            payload={"value": "custom"},
        )
    )
    return actions


def _level_display(level: str | None) -> str:
    if not level:
        return ""
    category = cl.user_session.get("role_category") or "tech"
    return LEVEL_LABELS.get(category, LEVEL_LABELS["tech"]).get(level, level)


def make_params_actions(category: str = "tech") -> list[cl.Action]:
    """All level + type + mode + done buttons on a single 'Параметры' screen.

    Visually grouped via icons; the picked value is reflected in the message
    text via `_update_params_message`.
    """
    labels = LEVEL_LABELS.get(category, LEVEL_LABELS["tech"])
    actions: list[cl.Action] = []
    for level in LEVELS:
        actions.append(
            cl.Action(
                name="param_level",
                label=f"📈 {labels.get(level, level)}",
                payload={"value": level},
            )
        )
    for key in INTERVIEW_TYPES:
        actions.append(
            cl.Action(
                name="param_type",
                label=_TYPE_SHORT_LABELS.get(key, key),
                payload={"value": key},
            )
        )
    for key, (label, _hint) in INTERVIEW_MODES.items():
        actions.append(
            cl.Action(
                name="param_mode",
                label=f"🚀 {label}",
                payload={"value": key},
            )
        )
    actions.append(
        cl.Action(
            name="param_done",
            label="✅ Готово →",
            payload={"value": "done"},
        )
    )
    return actions


def make_restart_action() -> list[cl.Action]:
    return [
        cl.Action(
            name="restart",
            label="🔄 Начать новое интервью",
            payload={"value": "restart"},
        )
    ]


def make_skills_skip_action() -> list[cl.Action]:
    return [
        cl.Action(
            name="skills_skip",
            label="⏭ Пропустить (без фокуса на скиллах)",
            payload={"value": "skip"},
        )
    ]


def make_finish_interview_action() -> list[cl.Action]:
    return [
        cl.Action(
            name="finish_interview",
            label="🏁 Завершить интервью",
            payload={"value": "finish"},
        )
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _init_session():
    cl.user_session.set("state", "select_role")
    cl.user_session.set("session_id", str(uuid.uuid4()))
    cl.user_session.set("role", None)
    cl.user_session.set("role_category", None)
    cl.user_session.set("level", None)
    cl.user_session.set("interview_type", None)
    cl.user_session.set("interview_mode", None)
    cl.user_session.set("question_num", 0)
    cl.user_session.set("questions", [])
    cl.user_session.set("answers", [])
    cl.user_session.set("scores", [])
    cl.user_session.set("questions_db", load_questions_db())
    cl.user_session.set("clarifications_used", 0)
    cl.user_session.set("pending_dont_know", False)
    cl.user_session.set("required_skills", [])
    cl.user_session.set("is_custom_role", False)
    cl.user_session.set("min_q", 0)
    cl.user_session.set("max_q", 0)
    cl.user_session.set("audio_buffer", None)
    cl.user_session.set("question_started_at", None)
    cl.user_session.set("question_timer_msg", None)
    cl.user_session.set("question_timer_task", None)
    cl.user_session.set("params_msg", None)


def _build_graph_state(current_step: str, **extra) -> dict:
    """Snapshot the live cl.user_session into an InterviewState dict for the
    LangGraph. Per-turn fields (user_input, elapsed_seconds, …) are passed via
    `extra` so callers can override without polluting cl.user_session."""
    state: dict = {
        "session_id": cl.user_session.get("session_id") or "",
        "role": cl.user_session.get("role"),
        "role_category": cl.user_session.get("role_category") or "tech",
        "level": cl.user_session.get("level"),
        "interview_type": cl.user_session.get("interview_type"),
        "interview_mode": cl.user_session.get("interview_mode"),
        "is_custom_role": bool(cl.user_session.get("is_custom_role")),
        "required_skills": list(cl.user_session.get("required_skills") or []),
        "questions": list(cl.user_session.get("questions") or []),
        "question_num": cl.user_session.get("question_num") or 0,
        "answers": list(cl.user_session.get("answers") or []),
        "scores": list(cl.user_session.get("scores") or []),
        "clarifications_used": cl.user_session.get("clarifications_used") or 0,
        "started_at": cl.user_session.get("started_at"),
        "summary": cl.user_session.get("summary"),
        "current_step": current_step,
    }
    state.update(extra)
    return {k: v for k, v in state.items() if v is not None}


async def _run_graph(current_step: str, **extra) -> dict:
    """Drive the LangGraph for one phase. Returns the resulting state dict."""
    state = _build_graph_state(current_step, **extra)
    return await asyncio.to_thread(_GRAPH.invoke, state)


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
    if score == 0:
        return "skipped", "⏭️", "Пропущено"
    if score >= 8:
        return "excellent", "✅", "Отлично"
    if score >= 6:
        return "good", "👍", "Хорошо"
    return "needs_work", "⚠️", "Можно лучше"


def _format_elapsed(seconds: float) -> str:
    """Человеко-читаемое время: '42 сек' / '2 мин 15 сек'."""
    total = max(0, int(round(seconds)))
    if total < 60:
        return f"{total} сек"
    minutes, sec = divmod(total, 60)
    if sec == 0:
        return f"{minutes} мин"
    return f"{minutes} мин {sec} сек"


def _format_clock(seconds: float) -> str:
    """Формат для живого счётчика: M:SS."""
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    return f"{minutes}:{sec:02d}"


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


async def _tick_timer(msg: cl.Message, started_at: float) -> None:
    """Раз в секунду обновляет сообщение-таймер. Завершается по cancel
    или по достижению TIMER_HARD_CAP_SEC."""
    try:
        while True:
            elapsed = time.monotonic() - started_at
            if elapsed >= TIMER_HARD_CAP_SEC:
                msg.content = f"⏱ {_format_clock(TIMER_HARD_CAP_SEC)} (таймер остановлен)"
                await msg.update()
                return
            msg.content = f"⏱ {_format_clock(elapsed)}"
            await msg.update()
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        return
    except Exception:
        log.exception("timer tick failed")


async def _start_question_timer() -> None:
    """Создаёт сообщение-таймер и фоновую задачу под текущий вопрос."""
    started_at = time.monotonic()
    timer_msg = await cl.Message(content="⏱ 0:00").send()
    task = asyncio.create_task(_tick_timer(timer_msg, started_at))
    cl.user_session.set("question_started_at", started_at)
    cl.user_session.set("question_timer_msg", timer_msg)
    cl.user_session.set("question_timer_task", task)


def _resume_question_timer() -> None:
    """Перезапускает тиканье на существующем сообщении-таймере без сброса started_at.
    Используется после уточнения / dont_know, когда пользователь продолжает работать
    над тем же вопросом."""
    timer_msg = cl.user_session.get("question_timer_msg")
    started_at = cl.user_session.get("question_started_at")
    if timer_msg is None or started_at is None:
        return
    task = asyncio.create_task(_tick_timer(timer_msg, started_at))
    cl.user_session.set("question_timer_task", task)


def _stop_question_timer() -> float:
    """Отменяет тикающую задачу и возвращает накопленное время.
    Само сообщение-таймер не трогает (его финализирует caller)."""
    task = cl.user_session.get("question_timer_task")
    if task is not None and not task.done():
        task.cancel()
    cl.user_session.set("question_timer_task", None)
    started_at = cl.user_session.get("question_started_at")
    if started_at is None:
        return 0.0
    return time.monotonic() - started_at


# ── Chainlit handlers ─────────────────────────────────────────────────────────

@cl.on_chat_start
async def on_chat_start():
    _init_session()
    await cl.Message(
        content=(
            "Добро пожаловать в **Сократ** — тренажёр собеседований.\n\n"
            "**Выбери целевую роль с помощью кнопок ниже:**"
        ),
        actions=make_role_actions(),
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    state = cl.user_session.get("state")

    if state == "in_interview":
        await _handle_answer(message.content)
    elif state == "finished":
        await cl.Message(
            content="Интервью завершено. Нажми **«Начать новое интервью»** выше, чтобы попробовать снова.",
            actions=make_restart_action(),
        ).send()
    elif state == "await_custom_role":
        await _handle_custom_role_input(message.content)
    elif state == "await_skills":
        await _handle_skills_input(message.content)
    elif state == "select_role":
        await _resend_selection_prompt(state)
    else:
        await _resend_selection_prompt(state)


async def _resend_selection_prompt(state: str):
    if state == "select_role":
        await cl.Message(
            content="Пожалуйста, **выбери роль** с помощью кнопок ниже:",
            actions=make_role_actions(),
        ).send()
    elif state == "select_params":
        await _render_params_screen()


async def _select_role(role: str):
    cl.user_session.set("role", role)
    cl.user_session.set("role_category", "tech")
    cl.user_session.set("is_custom_role", False)
    await _render_params_screen()


async def _start_custom_role_input():
    cl.user_session.set("state", "await_custom_role")
    await cl.Message(
        content=(
            "Окей, введи название должности текстом — например: "
            "`Middle DevOps Engineer`, `Senior Data Engineer`, `Junior QA Automation`."
        )
    ).send()


def _params_content(role: str | None) -> str:
    category = cl.user_session.get("role_category") or "tech"
    levels_lbl = LEVEL_LABELS.get(category, LEVEL_LABELS["tech"])
    level = cl.user_session.get("level")
    interview_type = cl.user_session.get("interview_type")
    interview_mode = cl.user_session.get("interview_mode")

    level_view = f"✓ **{levels_lbl.get(level, level)}**" if level else "—"
    type_view = f"✓ **{INTERVIEW_TYPES.get(interview_type, interview_type)}**" if interview_type else "—"
    if interview_mode:
        mlabel, mhint = INTERVIEW_MODES[interview_mode]
        mode_view = f"✓ **{mlabel}** _({mhint})_"
    else:
        mode_view = "—"

    return (
        f"### ⚙️ Настрой интервью\n\n"
        f"Роль: **{role}** ✓\n\n"
        f"Кликай по кнопкам ниже — выбери уровень, тип и режим. Когда всё готово, жми **Готово →**.\n\n"
        f"- 📈 **Уровень**: {level_view}\n"
        f"- 🧩 **Тип**: {type_view}\n"
        f"- 🚀 **Режим**: {mode_view}\n"
    )


async def _render_params_screen():
    cl.user_session.set("state", "select_params")
    role = cl.user_session.get("role")
    category = cl.user_session.get("role_category") or "tech"
    msg = cl.Message(
        content=_params_content(role),
        actions=make_params_actions(category),
    )
    await msg.send()
    cl.user_session.set("params_msg", msg)


async def _update_params_message():
    msg = cl.user_session.get("params_msg")
    if msg is None:
        return
    msg.content = _params_content(cl.user_session.get("role"))
    try:
        await msg.update()
    except Exception:
        log.debug("params msg update failed", exc_info=True)


async def _handle_custom_role_input(text: str):
    role = (text or "").strip()
    if len(role) < 3:
        await cl.Message(
            content="Слишком короткое название. Введи должность хотя бы из 3 символов (например: `Middle DevOps Engineer`)."
        ).send()
        return
    role = role[:_MAX_ROLE_LEN]
    cl.user_session.set("role", role)
    cl.user_session.set("is_custom_role", True)

    async with cl.Step(name="Определяю тип роли…", type="tool") as step:
        category = await asyncio.to_thread(classify_role, role)
        step.output = f"категория: {category}"
    cl.user_session.set("role_category", category)

    await _render_params_screen()


async def _handle_skills_input(text: str):
    skills = _parse_skills(text)
    cl.user_session.set("required_skills", skills)
    if skills:
        await cl.Message(
            content="Навыки: **" + ", ".join(skills) + "** ✓"
        ).send()
    else:
        await cl.Message(content="Не распознал навыки — поедем без специфичного фокуса.").send()
    await _start_interview()


@cl.action_callback("select_role")
async def on_select_role(action: cl.Action):
    value = action.payload["value"]
    await action.remove()
    await _select_role(value)


@cl.action_callback("select_role_custom")
async def on_select_role_custom(action: cl.Action):
    await action.remove()
    await _start_custom_role_input()


@cl.action_callback("param_level")
async def on_param_level(action: cl.Action):
    cl.user_session.set("level", action.payload["value"])
    await _update_params_message()


@cl.action_callback("param_type")
async def on_param_type(action: cl.Action):
    cl.user_session.set("interview_type", action.payload["value"])
    await _update_params_message()


@cl.action_callback("param_mode")
async def on_param_mode(action: cl.Action):
    cl.user_session.set("interview_mode", action.payload["value"])
    await _update_params_message()


@cl.action_callback("param_done")
async def on_param_done(action: cl.Action):
    level = cl.user_session.get("level")
    interview_type = cl.user_session.get("interview_type")
    interview_mode = cl.user_session.get("interview_mode")
    if not (level and interview_type and interview_mode):
        await cl.Message(
            content="Сначала выбери все три параметра: **уровень**, **тип** и **режим** — потом жми **Готово**.",
        ).send()
        return

    msg = cl.user_session.get("params_msg")
    if msg is not None:
        try:
            msg.actions = []
            await msg.update()
        except Exception:
            log.debug("params msg cleanup failed", exc_info=True)
    cl.user_session.set("params_msg", None)
    cl.user_session.set("state", "await_skills")

    await cl.Message(
        content=(
            "Параметры готовы! 🎉\n\n"
            "На каких **навыках/инструментах** сделать акцент? "
            "Перечисли через запятую (например: `Docker, Kubernetes, Terraform, AWS`).\n\n"
            "Или нажми кнопку ниже, чтобы пройти стандартное интервью без фокуса."
        ),
        actions=make_skills_skip_action(),
    ).send()


@cl.action_callback("skills_skip")
async def on_skills_skip(action: cl.Action):
    cl.user_session.set("required_skills", [])
    await action.remove()
    await _start_interview()


async def _start_interview():
    role = cl.user_session.get("role")
    level = cl.user_session.get("level")
    interview_type = cl.user_session.get("interview_type")
    role_category: str = cl.user_session.get("role_category") or "tech"
    required_skills: list[str] = cl.user_session.get("required_skills") or []
    is_custom_role: bool = bool(cl.user_session.get("is_custom_role"))
    db = cl.user_session.get("questions_db")

    mode = cl.user_session.get("interview_mode")
    min_q, max_q = compute_bounds(level, len(required_skills), mode)
    cl.user_session.set("min_q", min_q)
    cl.user_session.set("max_q", max_q)

    needs_llm = bool(required_skills) or is_custom_role
    if needs_llm:
        async with cl.Step(name="Генерирую персонализированные вопросы…", type="tool") as step:
            questions = await asyncio.to_thread(
                get_personalized_questions,
                db,
                role,
                level,
                interview_type,
                required_skills,
                is_custom_role,
                max_q,
                role_category,
            )
            step.output = f"подготовлено {len(questions)} вопросов"
    else:
        questions = get_personalized_questions(
            db, role, level, interview_type, required_skills, is_custom_role, max_q,
            role_category,
        )

    cl.user_session.set("questions", questions)
    cl.user_session.set("question_num", 0)
    cl.user_session.set("answers", [])
    cl.user_session.set("scores", [])
    cl.user_session.set("state", "in_interview")

    # Drive the dialog initialization through the LangGraph: greeting node
    # stamps started_at, collect_context validates the role/level/type set
    # earlier via Chainlit buttons. Failures here surface as state.errors.
    init_state = await _run_graph(PHASE_START)
    if init_state.get("started_at"):
        cl.user_session.set("started_at", init_state["started_at"])

    skills_line = (
        f"- Навыки в фокусе: **{', '.join(required_skills)}**\n"
        if required_skills
        else ""
    )
    if required_skills:
        length_explanation = (
            f"Интервью адаптивное: **от {min_q} до {max_q} вопросов**. "
            f"Закончим, когда каждый из навыков получит достаточно ответов "
            f"для уверенной оценки (или дойдём до верхней границы)."
        )
    else:
        length_explanation = (
            f"Интервью адаптивное: **от {min_q} до {max_q} вопросов**. "
            f"Закончим раньше, если уровень станет очевиден по нескольким "
            f"подряд идущим ответам."
        )
    await cl.Message(
        content=(
            f"**Параметры интервью**\n\n"
            f"- Роль: **{role}**\n"
            f"- Уровень: **{_level_display(level)}**\n"
            f"- Тип: **{INTERVIEW_TYPES[interview_type]}**\n"
            f"{skills_line}\n"
            f"{length_explanation} После каждого ответа — мгновенный фидбэк.\n\n"
            "Отвечай развёрнуто, как на настоящем интервью. Поехали! 🚀"
        )
    ).send()

    await _ask_next_question()

    # Background bank enrichment only for the standard combos (no skills, known role).
    if not required_skills and not is_custom_role:
        existing_texts = [
            q["question"]
            for q in db.get("roles", {})
                       .get(role, {})
                       .get(level, {})
                       .get(interview_type, [])
            if isinstance(q, dict) and q.get("question")
        ]
        asyncio.create_task(
            _enrich_bank_async(role, level, interview_type, existing_texts)
        )


@cl.action_callback("finish_interview")
async def on_finish_interview(action: cl.Action):
    await action.remove()
    if cl.user_session.get("state") != "in_interview":
        return

    _stop_question_timer()
    timer_msg = cl.user_session.get("question_timer_msg")
    if timer_msg is not None:
        try:
            await timer_msg.remove()
        except Exception:
            log.debug("timer message remove failed", exc_info=True)
    cl.user_session.set("question_timer_msg", None)
    cl.user_session.set("question_started_at", None)

    await _finish_interview()


@cl.action_callback("restart")
async def on_restart(action: cl.Action):
    await action.remove()
    _init_session()
    await cl.Message(
        content=(
            "Начинаем заново!\n\n"
            "**Выбери целевую роль:**"
        ),
        actions=make_role_actions(),
    ).send()


# ── Interview flow ────────────────────────────────────────────────────────────

async def _ask_next_question():
    question_num = cl.user_session.get("question_num")
    questions = cl.user_session.get("questions")
    min_q = cl.user_session.get("min_q") or 0
    max_q = cl.user_session.get("max_q") or len(questions)
    scores = cl.user_session.get("scores") or []
    required_skills: list[str] = cl.user_session.get("required_skills") or []

    if should_stop_interview(question_num, min_q, max_q, scores, required_skills) \
            or question_num >= len(questions):
        await _finish_interview()
        return

    # Pick the next question through the LangGraph (interview node).
    next_state = await _run_graph(PHASE_NEXT_QUESTION)
    q = next_state.get("current_question") or questions[question_num]
    interview_type = cl.user_session.get("interview_type", "")
    type_tag = TYPE_LABEL.get(interview_type, interview_type)
    emoji = TYPE_EMOJI.get(interview_type, "🎯")

    filled = question_num + 1
    bar_total = max(filled, max_q)
    bar = "▓" * filled + "░" * max(0, bar_total - filled)

    await cl.Message(
        content=(
            f"{emoji} **Вопрос {filled}/{max_q}** · {type_tag}\n\n"
            f"`{bar}`\n\n"
            f"{q['question']}"
        ),
        actions=make_finish_interview_action(),
    ).send()

    await _start_question_timer()


async def _handle_answer(answer_text: str):
    question_num = cl.user_session.get("question_num")
    questions = cl.user_session.get("questions")

    if question_num >= len(questions):
        return

    elapsed_seconds = _stop_question_timer()

    question = questions[question_num]

    try:
        async with cl.Step(name="Анализ ответа", type="tool") as step:
            # Route the LLM analysis through the LangGraph analyze_answer node.
            # The node also runs filter_skills_touched on the result.
            result_state = await _run_graph(
                PHASE_ANALYZE,
                user_input=answer_text,
                elapsed_seconds=elapsed_seconds,
                current_question=question,
            )
            last_analysis = result_state.get("last_analysis")
            if not last_analysis:
                raise RuntimeError("analyze_answer node returned no analysis")
            analysis = AnswerEvaluation.model_validate(last_analysis)
            if analysis.score is not None:
                _, emoji, label = verdict_for_score(analysis.score)
                step.output = f"{emoji} {label} · {analysis.score}/10 · intent={analysis.intent}"
            else:
                step.output = f"intent={analysis.intent} (без оценки)"
    except Exception:  # noqa: BLE001
        await cl.Message(
            content=(
                "Что-то пошло не так с анализом ответа. "
                "Попробуй сформулировать ещё раз — или иначе."
            )
        ).send()
        return

    if analysis.intent == "clarification":
        await _handle_clarification(
            analysis, cl.user_session.get("clarifications_used") or 0
        )
        return

    if analysis.intent == "dont_know":
        if cl.user_session.get("pending_dont_know"):
            await _record_and_advance(question, answer_text, analysis, score_override=0)
            return
        await _handle_dont_know(analysis)
        return

    if analysis.intent == "meta":
        await cl.Message(content=analysis.feedback).send()
        _resume_question_timer()
        return

    if analysis.intent == "skip":
        await _record_and_advance(question, answer_text, analysis, score_override=0)
        return

    # intent == "answer"
    score = analysis.score if analysis.score is not None else 1
    await _record_and_advance(question, answer_text, analysis, score_override=score)


async def _handle_clarification(analysis: AnswerEvaluation, clarifications_used: int):
    cl.user_session.set("clarifications_used", clarifications_used + 1)
    parts = []
    if analysis.feedback:
        parts.append(analysis.feedback)
    if analysis.explanation:
        parts.append(analysis.explanation)
    await cl.Message(content="\n\n".join(parts) or "Давай разберём вопрос.").send()
    _resume_question_timer()


async def _handle_dont_know(analysis: AnswerEvaluation):
    cl.user_session.set("pending_dont_know", True)
    await cl.Message(content=analysis.feedback).send()
    _resume_question_timer()


async def _record_and_advance(
    question: dict,
    answer_text: str,
    analysis: AnswerEvaluation,
    score_override: int,
):
    answers: list = cl.user_session.get("answers")
    scores: list = cl.user_session.get("scores")
    question_num = cl.user_session.get("question_num")

    _, emoji, label = verdict_for_score(score_override)

    # Финализируем таймер: время уже накоплено в started_at; задача отменена
    # ещё на входе в _handle_answer. Удалим сообщение-счётчик из чата.
    started_at = cl.user_session.get("question_started_at")
    elapsed = (time.monotonic() - started_at) if started_at is not None else 0.0
    pace = _pace_score(elapsed) if score_override > 0 else None

    timer_msg = cl.user_session.get("question_timer_msg")
    if timer_msg is not None:
        try:
            await timer_msg.remove()
        except Exception:
            log.debug("timer message remove failed", exc_info=True)
    cl.user_session.set("question_timer_msg", None)
    cl.user_session.set("question_started_at", None)

    answers.append(answer_text)
    scores.append(
        {
            "score": score_override,
            "verdict": label,
            "feedback": analysis.feedback,
            "strengths": analysis.strengths,
            "weaknesses": analysis.weaknesses,
            "category": analysis.category,
            "intent": analysis.intent,
            "skills_touched": list(analysis.skills_touched or []),
            "question_id": question.get("id", ""),
            "role_fit": analysis.role_fit,
            "structure": analysis.structure,
            "literacy": analysis.literacy,
            "oratory": analysis.oratory,
            "depth": analysis.depth,
            "pace": pace,
            "elapsed_seconds": round(elapsed, 1),
            "ideal_answer": analysis.ideal_answer,
        }
    )

    cl.user_session.set("answers", answers)
    cl.user_session.set("scores", scores)
    cl.user_session.set("question_num", question_num + 1)
    cl.user_session.set("clarifications_used", 0)
    cl.user_session.set("pending_dont_know", False)

    time_line = f"⏱ {_format_elapsed(elapsed)}"
    if pace is not None:
        time_line += f" · темп {pace}/10"

    if score_override == 0:
        header = f"{emoji} **{label}**"
    else:
        header = f"{emoji} **{label}** · {score_override}/10"

    message = f"{header}\n\n---\n\n{analysis.feedback}\n\n{time_line}"
    await cl.Message(content=message).send()

    # Если ответ слабый/пропущен/«не знаю» — показываем эталонный ответ как карточку (blockquote).
    if analysis.ideal_answer and (score_override < 6):
        ideal_lines = analysis.ideal_answer.split("\n")
        quoted = "\n".join(f"> {line}" if line else ">" for line in ideal_lines)
        await cl.Message(
            content=(
                "> 💡 **Как ответил бы сильный кандидат**\n"
                ">\n"
                f"{quoted}"
            )
        ).send()

    await _ask_next_question()


async def _finish_interview():
    cl.user_session.set("state", "finished")

    role = cl.user_session.get("role")
    level = cl.user_session.get("level")
    interview_type = cl.user_session.get("interview_type")
    role_category: str = cl.user_session.get("role_category") or "tech"
    questions = cl.user_session.get("questions")
    answers = cl.user_session.get("answers")
    scores = cl.user_session.get("scores")
    session_id = cl.user_session.get("session_id")
    required_skills: list[str] = cl.user_session.get("required_skills") or []

    await cl.Message(content="Интервью завершено! Формирую отчёт...").send()

    async with cl.Step(name="Генерация итогового отчёта", type="tool") as step:
        # Summary computation runs through the LangGraph summarize node.
        finalize_state = await _run_graph(PHASE_FINALIZE)
        summary = finalize_state.get("summary") or compute_summary(
            role, level, interview_type, questions, answers, scores, required_skills
        )
        step.output = (
            f"Итог: {summary['total_score']}/{summary['max_possible']} "
            f"({summary['percentage']}%) — {summary['verdict']}"
        )

    weak_topics = _extract_weak_topics(scores, questions, summary.get("skills_coverage") or [])
    resources: list[dict] = []
    if weak_topics:
        try:
            async with cl.Step(name="Подбираю материалы по слабым темам…", type="tool") as step:
                resources = await asyncio.to_thread(
                    generate_resources, role, level, weak_topics, role_category
                )
                step.output = f"подобрано тем: {len(resources)}"
        except Exception:
            log.exception("resource generation failed (best-effort, ignored)")
            resources = []
    summary["resources"] = resources
    cl.user_session.set("summary", summary)

    # Format report
    type_label = INTERVIEW_TYPES.get(interview_type, interview_type)

    strengths_text = (
        "\n".join(f"- {q}" for q in summary["strengths"])
        if summary["strengths"]
        else "- Продолжай практиковаться для достижения отличных результатов"
    )
    improvements_text = (
        "\n".join(f"- {q}" for q in summary["improvements"])
        if summary["improvements"]
        else "- Отличный результат! Явных слабых мест не выявлено"
    )

    per_q_lines = []
    for i, (q, s) in enumerate(zip(questions, scores), 1):
        _, e, _ = verdict_for_score(s["score"])
        elapsed = s.get("elapsed_seconds")
        time_part = (
            f" _({_format_clock(elapsed)})_"
            if isinstance(elapsed, (int, float))
            else ""
        )
        snippet = q["question"][:60].rstrip()
        if s["score"] == 0:
            per_q_lines.append(
                f"{i}. {e} **пропущено**{time_part} — {snippet}…"
            )
        else:
            per_q_lines.append(
                f"{i}. {e} **{s['score']}/10**{time_part} — {snippet}…"
            )

    per_q_text = "\n".join(per_q_lines)

    skills_header_text = (
        f" · **Навыки:** {', '.join(required_skills)}" if required_skills else ""
    )

    dimensions = summary.get("dimensions") or {}
    has_dimension_data = any(v > 0 for v in dimensions.values())
    dimensions_section = ""
    if has_dimension_data:
        dimension_lines = [
            f"- **{DIMENSION_LABELS[k]}** — {dimensions.get(k, 0.0)}/10"
            for k in DIMENSION_LABELS
        ]
        dimensions_section = (
            "### 📊 Профиль интервью\n" + "\n".join(dimension_lines) + "\n\n"
        )

    pace_section = ""
    total_time = summary.get("total_time_seconds") or 0.0
    avg_time = summary.get("avg_time_seconds") or 0.0
    if total_time > 0:
        pace_section = (
            "### ⏱ Темп ответов\n"
            f"- Общее время: **{_format_elapsed(total_time)}**\n"
            f"- Среднее на ответ: **{_format_elapsed(avg_time)}**\n\n"
        )

    skills_section = ""
    if summary.get("skills_coverage"):
        coverage_lines = []
        for entry in summary["skills_coverage"]:
            if entry["questions_count"] == 0:
                coverage_lines.append(
                    f"- **{entry['skill']}** — не раскрыт в ответах ⚠️"
                )
            else:
                _, e, _ = verdict_for_score(int(round(entry["avg_score"])))
                coverage_lines.append(
                    f"- **{entry['skill']}** — {e} {entry['avg_score']}/10 "
                    f"(вопросов: {entry['questions_count']})"
                )
        skills_section = "### 🎯 Покрытие навыков\n" + "\n".join(coverage_lines) + "\n\n"

    resources_section = _format_resources_section(resources)

    # ── Hero block: верховный вердикт. ────────────────────────────────────────
    hero_emoji = {"Strong": "🎉", "Competent": "💪", "Needs Work": "🔧"}.get(
        summary["verdict"], "✨"
    )
    if summary["counted_count"] == 0:
        hero_block = (
            "## 🔧 Все вопросы пропущены\n\n"
            "Попробуй пройти ещё раз — отвечай хоть как-нибудь, даже короткие ответы помогут оценить уровень."
        )
    else:
        skipped_note = (
            f" _(учтено {summary['counted_count']} из {len(scores)}, "
            f"пропущено: {summary['skipped_count']})_"
            if summary["skipped_count"] > 0
            else ""
        )
        hero_block = (
            f"## {hero_emoji} {summary['verdict']} — {summary['percentage']}%\n\n"
            f"**Роль:** {role} · **Уровень:** {_level_display(level)} · **Тип:** {type_label}{skills_header_text}\n\n"
            f"_{summary['total_score']}/{summary['max_possible']} баллов_{skipped_note}"
        )
    await cl.Message(content=hero_block).send()

    # ── Радар: отдельным сообщением, чтобы график был визуально выше текста. ──
    if has_dimension_data:
        radar_fig = _build_radar_figure(dimensions)
        if radar_fig is not None:
            await cl.Message(
                content="### 📊 Профиль интервью",
                elements=[
                    cl.Plotly(name="profile_radar", figure=radar_fig, display="inline")
                ],
            ).send()

    # ── Текстовый отчёт: всё, кроме hero и графика. ───────────────────────────
    report = (
        f"{dimensions_section}"
        f"{pace_section}"
        f"### ✅ Сильные стороны\n{strengths_text}\n\n"
        f"### 🛠 Над чем поработать\n{improvements_text}\n\n"
        f"{skills_section}"
        f"{resources_section}"
        f"### 📋 По вопросам\n{per_q_text}\n\n"
        f"---\n\n"
        f"### 💡 Рекомендация\n{summary['recommendation']}\n\n"
        f"---\n\n"
        f"_Session ID: `{session_id}`_"
    )
    await cl.Message(content=report).send()

    # Persist the session through the LangGraph save_session node.
    persist_state = await _run_graph(PHASE_PERSIST)
    saved_path = persist_state.get("saved_path")
    if saved_path:
        await cl.Message(
            content=f"Сессия сохранена в `{saved_path}`",
            actions=make_restart_action(),
        ).send()
    else:
        await cl.Message(
            content="⚠️ Не удалось сохранить сессию, но отчёт выше — твой результат.",
            actions=make_restart_action(),
        ).send()


# ── Voice input (SaluteSpeech) ────────────────────────────────────────────────

@cl.on_audio_start
async def on_audio_start() -> bool:
    """Разрешаем запись только если STT настроен."""
    if get_stt_client() is None:
        await cl.Message(
            content=(
                "🎤 Голосовой ввод не настроен. "
                "Добавь `SBER_SALUTE_AUTH_KEY` в `.env` и перезапусти приложение."
            )
        ).send()
        return False
    cl.user_session.set("audio_buffer", None)
    return True


@cl.on_audio_chunk
async def on_audio_chunk(chunk: cl.InputAudioChunk) -> None:
    # Chainlit 2.x dispatches each chunk via asyncio.create_task without
    # awaiting, so the previous list-replace pattern (`get → append → set`)
    # was racy and on_audio_end could observe an empty list. The fix is the
    # official cookbook pattern: a single BytesIO created on isStart and
    # mutated in place by every subsequent chunk.
    buffer: BytesIO | None = cl.user_session.get("audio_buffer")
    if getattr(chunk, "isStart", False) or buffer is None:
        buffer = BytesIO()
        cl.user_session.set("audio_buffer", buffer)
        cl.user_session.set("audio_mime_type", getattr(chunk, "mimeType", ""))
    buffer.write(chunk.data)


@cl.on_audio_end
async def on_audio_end(*_args, **_kwargs) -> None:
    buffer: BytesIO | None = cl.user_session.get("audio_buffer")
    cl.user_session.set("audio_buffer", None)
    audio_bytes = buffer.getvalue() if buffer is not None else b""
    if not audio_bytes:
        await cl.Message(content="Пустая запись — попробуй ещё раз.").send()
        return

    client = get_stt_client()
    if client is None:
        await cl.Message(
            content=(
                "🎤 Голосовой ввод не настроен. "
                "Добавь `SBER_SALUTE_AUTH_KEY` в `.env` и перезапусти приложение."
            )
        ).send()
        return

    try:
        async with cl.Step(name="Распознаю речь (SaluteSpeech)…", type="tool") as step:
            text = await asyncio.to_thread(
                client.recognize_pcm16, audio_bytes, AUDIO_SAMPLE_RATE
            )
            step.output = (
                f"распознано символов: {len(text)}" if text else "пусто"
            )
    except SaluteSpeechError as exc:
        log.exception("SaluteSpeech transcription failed")
        await cl.Message(content=f"❌ Ошибка распознавания: {exc}").send()
        return
    except Exception:
        log.exception("STT failed")
        await cl.Message(
            content="❌ Не удалось распознать речь. Попробуй ещё раз."
        ).send()
        return

    if not text:
        await cl.Message(
            content="Не удалось разобрать запись — попробуй сказать чуть громче или ближе к микрофону."
        ).send()
        return

    await cl.Message(content=f"🎤 _Распознано:_ {text}").send()

    state = cl.user_session.get("state")
    if state == "in_interview":
        await _handle_answer(text)
    elif state == "await_custom_role":
        await _handle_custom_role_input(text)
    elif state == "await_skills":
        await _handle_skills_input(text)
    else:
        await _resend_selection_prompt(state)
