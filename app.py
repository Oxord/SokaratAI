import asyncio
import json
import logging
import os
import random
import re
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
from sokrat.company_profile import CompanyProfile, build_company_profile
from sokrat.gap_analysis import GapAnalysis, analyze_gaps
from sokrat.history import build_dynamics, format_dynamics, other_vacancies, recent_weak_topics
from sokrat.users import authenticate
from sokrat.personas import (
    DEFAULT_PERSONA,
    PERSONAS,
    STRESS_TIME_LIMIT_SEC,
    feedback_style,
    get_persona,
    language_feedback_directive,
    language_question_directive,
    question_style,
    suggest_persona,
)
from sokrat.practice import (
    KINDS as PRACTICE_KINDS,
    PracticeError,
    PracticeTask,
    evaluate_solution,
    format_evaluation,
    format_task,
    generate_task,
)
from sokrat.llm import llm_configured
from sokrat.interview_rules import (
    BANK_REFRESH_PER_SESSION,
    FALLBACK_QUESTIONS,
    INTERVIEW_MODES,
    INTERVIEW_TYPES,
    LEVEL_LABELS,
    LEVELS,
    QUESTIONS_PATH,
    ROLES,
    TYPE_EMOJI,
    TYPE_LABEL,
    _MAX_ROLE_LEN,
    _append_questions_to_bank,
    _extract_weak_topics,
    _pace_score,
    _parse_skills,
    compute_bounds,
    get_personalized_questions,
    load_questions_db,
    should_stop_interview,
    verdict_for_score,
)
from sokrat.question_generator import generate_questions
from sokrat.readiness import compute_readiness
from sokrat.resume_parser import (
    ResumeParseError,
    ResumeProfile,
    extract_resume_text,
    parse_resume,
)
from sokrat.resource_generator import generate_resources
from sokrat.role_classifier import classify_role
from sokrat.stt import SaluteSpeechError, get_stt_client, tts_enabled
from sokrat.star_analysis import (
    STAR_PARTS as STAR_LABELS,
    aggregate_star,
    analyze_star,
    format_star_block,
    is_behavioral,
)
from sokrat.study_plan import (
    StudyPlanError,
    build_study_plan,
    collect_focus_areas,
    format_study_plan,
)
from sokrat.summary_builder import compute_summary
from sokrat.vacancy_parser import VacancyContext, VacancyParseError, extract_vacancy_context

_GRAPH = build_graph()

log = logging.getLogger(__name__)

load_dotenv()

SESSION_DIR = Path(os.getenv("SESSION_DIR", "data/sessions"))

# Должно совпадать с [features.audio].sample_rate в .chainlit/config.toml.
AUDIO_SAMPLE_RATE = 16000











# ── Data loading ──────────────────────────────────────────────────────────────







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


def _build_progress_figure(points: list[float]):
    """Линия прогресса по сессиям одной роли (процент баллов)."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None
    xs = list(range(1, len(points) + 1))
    fig = go.Figure(
        data=[
            go.Scatter(
                x=xs,
                y=points,
                mode="lines+markers",
                line=dict(color="#5B8DEF", width=3),
                marker=dict(size=9),
            )
        ]
    )
    fig.update_layout(
        xaxis=dict(title="Сессия", tickmode="array", tickvals=xs),
        yaxis=dict(title="Результат, %", range=[0, 100]),
        showlegend=False,
        margin=dict(l=50, r=30, t=40, b=50),
        title="Прогресс по роли",
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
    actions.append(
        cl.Action(
            name="select_role_vacancy",
            label="📎 По ссылке на вакансию",
            payload={"value": "vacancy"},
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
    for key, persona in PERSONAS.items():
        actions.append(
            cl.Action(
                name="param_persona",
                label=persona.label,
                payload={"value": key},
            )
        )
    actions.append(
        cl.Action(
            name="param_lang",
            label="🌐 English / Русский",
            payload={"value": "toggle"},
        )
    )
    actions.append(
        cl.Action(
            name="param_stress",
            label="🔥 Стресс-режим вкл/выкл",
            payload={"value": "toggle"},
        )
    )
    actions.append(
        cl.Action(
            name="upload_resume",
            label="📄 Загрузить резюме (персонализация)",
            payload={"value": "resume"},
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


def make_report_actions() -> list[cl.Action]:
    return [
        cl.Action(
            name="study_plan",
            label="📅 План подготовки на 7 дней",
            payload={"value": "7"},
        ),
        cl.Action(
            name="download_report",
            label="📄 Скачать отчёт (Markdown)",
            payload={"value": "md"},
        ),
        cl.Action(
            name="practice",
            label="🧪 Задача на код",
            payload={"value": "code"},
        ),
        cl.Action(
            name="practice",
            label="🏗 System design",
            payload={"value": "design"},
        ),
        *make_restart_action(),
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

def _current_user_id() -> str:
    user = cl.user_session.get("user")
    return str(getattr(user, "identifier", "") or "")


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
    cl.user_session.set("job_context", None)
    cl.user_session.set("company_profile", None)
    cl.user_session.set("resume_profile", None)
    cl.user_session.set("gap_analysis", None)
    cl.user_session.set("focus_areas", [])
    cl.user_session.set("persona", None)
    cl.user_session.set("stress_mode", False)
    cl.user_session.set("practice_task", None)
    cl.user_session.set("lang", "ru")
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
        "job_vacancy": (
            {
                "company": job_ctx.company,
                "source": job_ctx.source,
                "company_description": job_ctx.company_description,
            }
            if (job_ctx := cl.user_session.get("job_context"))
            else None
        ),
        "company_profile": (
            cp.to_dict() if (cp := cl.user_session.get("company_profile")) else None
        ),
        "resume_profile": (
            rp.to_dict() if (rp := cl.user_session.get("resume_profile")) else None
        ),
        "gap_analysis": (
            ga.to_dict() if (ga := cl.user_session.get("gap_analysis")) else None
        ),
        "user_id": _current_user_id(),
        "interviewer_persona": cl.user_session.get("persona") or DEFAULT_PERSONA,
        "stress_mode": bool(cl.user_session.get("stress_mode")),
        "persona_style": " ".join(
            part
            for part in (
                feedback_style(
                    cl.user_session.get("persona"), bool(cl.user_session.get("stress_mode"))
                ),
                language_feedback_directive(cl.user_session.get("lang")),
            )
            if part
        ),
        "current_step": current_step,
    }
    # В стресс-режиме уточнения не даём: лимит анализатора считается исчерпанным.
    if cl.user_session.get("stress_mode"):
        state["clarifications_used"] = max(state["clarifications_used"], 2)
    state.update(extra)
    return {k: v for k, v in state.items() if v is not None}


async def _run_graph(current_step: str, **extra) -> dict:
    """Drive the LangGraph for one phase. Returns the resulting state dict."""
    state = _build_graph_state(current_step, **extra)
    return await asyncio.to_thread(_GRAPH.invoke, state)










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




async def _tick_timer(msg: cl.Message, started_at: float) -> None:
    """Раз в секунду обновляет сообщение-таймер. Завершается по cancel
    или по достижению TIMER_HARD_CAP_SEC. В стресс-режиме по истечении лимита
    один раз давит на кандидата и помечает таймер."""
    stress = bool(cl.user_session.get("stress_mode"))
    try:
        while True:
            elapsed = time.monotonic() - started_at
            if stress and STRESS_TIME_LIMIT_SEC <= elapsed < TIMER_HARD_CAP_SEC:
                question_num = cl.user_session.get("question_num")
                if cl.user_session.get("stress_warned_for") != question_num:
                    cl.user_session.set("stress_warned_for", question_num)
                    await cl.Message(
                        content="⏰ **Время вышло.** На реальном интервью тебя бы уже "
                        "перебили — заканчивай мысль и давай итог."
                    ).send()
                msg.content = f"🔥 ⏱ {_format_clock(elapsed)} — лимит {STRESS_TIME_LIMIT_SEC} сек превышен"
                await msg.update()
                await asyncio.sleep(1)
                continue
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

if os.getenv("CHAINLIT_AUTH_SECRET"):
    # Вход по логину/паролю включается, только если задан CHAINLIT_AUTH_SECRET.
    # Без него приложение работает анонимно (история общая, как раньше).
    _ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "true").lower() != "false"

    @cl.password_auth_callback
    def _auth_callback(username: str, password: str):
        if authenticate(username, password, allow_signup=_ALLOW_SIGNUP):
            return cl.User(identifier=username.strip().lower(), metadata={"role": "user"})
        return None


@cl.on_chat_start
async def on_chat_start():
    _init_session()
    if not llm_configured():
        await cl.Message(
            content=(
                "⚠️ **LLM-провайдер не настроен**: задай `DEEPSEEK_API_KEY` в `.env` "
                "и перезапусти приложение — без него вопросы и оценка не будут работать."
            )
        ).send()
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
    elif state == "await_practice":
        await _handle_practice_solution(message.content)
    elif state == "await_custom_role":
        await _handle_custom_role_input(message.content)
    elif state == "await_skills":
        await _handle_skills_input(message.content)
    elif state == "await_vacancy_input":
        await _handle_vacancy_input(message.content)
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
    elif state == "await_vacancy_input":
        await cl.Message(
            content="Пришли ссылку на вакансию (например, с hh.ru) или вставь текст вакансии целиком."
        ).send()


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


async def _start_vacancy_input():
    cl.user_session.set("state", "await_vacancy_input")
    await cl.Message(
        content=(
            "Пришли **ссылку на вакансию** (например, с hh.ru) — или, если сайт не открывается "
            "у меня, просто скопируй и вставь текст вакансии целиком.\n\n"
            "Я подберу роль, уровень, тип интервью и стек под конкретную вакансию, "
            "чтобы вопросы были про реальную работу и компанию, а не абстрактные."
        )
    ).send()


async def _handle_vacancy_input(text: str):
    raw = (text or "").strip()
    if len(raw) < 8:
        await cl.Message(
            content="Пришли ссылку на вакансию или вставь текст вакансии целиком (хотя бы пару предложений)."
        ).send()
        return

    async with cl.Step(name="Анализирую вакансию…", type="tool") as step:
        try:
            ctx = await asyncio.to_thread(extract_vacancy_context, raw)
        except VacancyParseError as exc:
            step.output = f"ошибка: {exc}"
            await cl.Message(
                content=(
                    f"Не получилось разобрать вакансию ({exc}).\n\n"
                    "Попробуй вставить **текст вакансии целиком** (скопируй со страницы), "
                    "или напиши название должности вручную — например: `Middle Backend Developer`."
                )
            ).send()
            return
        step.output = (
            f"роль: {ctx.title or '—'} · компания: {ctx.company or '—'} · источник: {ctx.source}"
        )

    role = ctx.title or raw[:_MAX_ROLE_LEN]
    cl.user_session.set("role", role)
    cl.user_session.set("is_custom_role", True)
    cl.user_session.set("job_context", ctx)
    if ctx.level_guess:
        cl.user_session.set("level", ctx.level_guess)
    if ctx.interview_type_guess:
        cl.user_session.set("interview_type", ctx.interview_type_guess)
    if ctx.skills:
        cl.user_session.set("required_skills", ctx.skills)

    async with cl.Step(name="Определяю тип роли…", type="tool") as step:
        category = await asyncio.to_thread(classify_role, role)
        step.output = f"категория: {category}"
    cl.user_session.set("role_category", category)

    company_profile: CompanyProfile | None = None
    if ctx.company:
        async with cl.Step(name="Собираю профиль компании…", type="tool") as step:
            company_profile = await asyncio.to_thread(build_company_profile, ctx.company, ctx)
            step.output = (
                "источники: " + ", ".join(company_profile.sources)
                if company_profile.sources
                else "открытых данных о компании не найдено"
            )
        if not company_profile.is_empty():
            cl.user_session.set("company_profile", company_profile)
            if not cl.user_session.get("persona"):
                suggested = suggest_persona(
                    company_profile.industry, ctx.company_description
                )
                if suggested != DEFAULT_PERSONA:
                    cl.user_session.set("persona", suggested)

    summary_lines = [
        f"Нашёл вакансию: **{role}**" + (f" — _{ctx.company}_" if ctx.company else "") + " ✅"
    ]
    if ctx.skills:
        summary_lines.append(f"- Стек из вакансии: **{', '.join(ctx.skills)}**")
    if ctx.responsibilities:
        summary_lines.append("- Обязанности: " + "; ".join(ctx.responsibilities[:3]))
    if company_profile and not company_profile.is_empty():
        summary_lines.append(f"\n**🏢 О компании {company_profile.name}**")
        if company_profile.industry:
            summary_lines.append(f"- Сфера: {company_profile.industry}")
        if company_profile.products:
            summary_lines.append(f"- Продукты: {', '.join(company_profile.products)}")
        if company_profile.tech_stack:
            summary_lines.append(f"- Технологии: {', '.join(company_profile.tech_stack)}")
        if company_profile.sources:
            summary_lines.append(f"_Источники: {'; '.join(company_profile.sources)}_")
        summary_lines.append(
            "_Вопросы будут опираться на эти данные. Если что-то выглядит неверно — "
            "это взято из открытых источников выше._"
        )
    summary_lines.append(
        "\nУровень, тип интервью и фокус-навыки уже подобраны по вакансии — "
        "проверь/поправь ниже и жми **Готово**."
    )
    await cl.Message(content="\n".join(summary_lines)).send()

    await _render_params_screen()


def _params_content(role: str | None) -> str:
    category = cl.user_session.get("role_category") or "tech"
    levels_lbl = LEVEL_LABELS.get(category, LEVEL_LABELS["tech"])
    level = cl.user_session.get("level")
    interview_type = cl.user_session.get("interview_type")
    interview_mode = cl.user_session.get("interview_mode")
    job_ctx: VacancyContext | None = cl.user_session.get("job_context")

    level_view = f"✓ **{levels_lbl.get(level, level)}**" if level else "—"
    type_view = f"✓ **{INTERVIEW_TYPES.get(interview_type, interview_type)}**" if interview_type else "—"
    if interview_mode:
        mlabel, mhint = INTERVIEW_MODES[interview_mode]
        mode_view = f"✓ **{mlabel}** _({mhint})_"
    else:
        mode_view = "—"
    vacancy_view = f"\n📎 По вакансии: **{job_ctx.company}**\n" if job_ctx and job_ctx.company else ""
    persona = get_persona(cl.user_session.get("persona"))
    persona_view = f"✓ **{persona.label}** _({persona.hint})_"
    stress = bool(cl.user_session.get("stress_mode"))
    resume: ResumeProfile | None = cl.user_session.get("resume_profile")
    resume_view = (
        f"\n📄 Резюме учтено: **{resume.headline or 'загружено'}**\n" if resume else ""
    )

    return (
        f"### ⚙️ Настрой интервью\n\n"
        f"Роль: **{role}** ✓\n"
        f"{vacancy_view}{resume_view}\n"
        f"Кликай по кнопкам ниже — выбери уровень, тип и режим. Когда всё готово, жми **Готово →**.\n\n"
        f"- 📈 **Уровень**: {level_view}\n"
        f"- 🧩 **Тип**: {type_view}\n"
        f"- 🚀 **Режим**: {mode_view}\n"
        f"- 🎭 **Интервьюер**: {persona_view}\n"
        f"- 🌐 **Язык интервью**: {'**English**' if cl.user_session.get('lang') == 'en' else 'русский'}\n"
        f"- 🔥 **Стресс-режим**: {'**включён** _(лимит 90 сек, без уточнений, каверзные вопросы)_' if stress else 'выкл'}\n"
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


@cl.action_callback("select_role_vacancy")
async def on_select_role_vacancy(action: cl.Action):
    await action.remove()
    await _start_vacancy_input()


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


@cl.action_callback("param_persona")
async def on_param_persona(action: cl.Action):
    key = action.payload["value"]
    if key in PERSONAS:
        cl.user_session.set("persona", key)
    await _update_params_message()


@cl.action_callback("param_lang")
async def on_param_lang(action: cl.Action):
    current = cl.user_session.get("lang") or "ru"
    cl.user_session.set("lang", "en" if current == "ru" else "ru")
    await _update_params_message()


@cl.action_callback("param_stress")
async def on_param_stress(action: cl.Action):
    cl.user_session.set("stress_mode", not cl.user_session.get("stress_mode"))
    await _update_params_message()


@cl.action_callback("upload_resume")
async def on_upload_resume(action: cl.Action):
    if cl.user_session.get("state") != "select_params":
        return

    files = await cl.AskFileMessage(
        content=(
            "📄 Загрузи резюме (**PDF, DOCX или TXT**, до 5 МБ). Контакты вырезаются до "
            "обработки, сам файл не сохраняется — остаётся только структурированный профиль."
        ),
        accept=[
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            ".pdf",
            ".docx",
            ".txt",
        ],
        max_size_mb=5,
        timeout=180,
    ).send()
    if not files:
        await cl.Message(content="Файл не получен — продолжаем без резюме.").send()
        return

    try:
        async with cl.Step(name="Разбираю резюме…", type="tool") as step:
            text = await asyncio.to_thread(extract_resume_text, files[0].path)
            profile = await asyncio.to_thread(parse_resume, text)
            step.output = (
                f"навыков: {len(profile.skills)}, мест работы: {len(profile.experience)}"
            )
    except ResumeParseError as exc:
        await cl.Message(
            content=f"⚠️ Не получилось разобрать резюме: {exc}. Продолжаем без него."
        ).send()
        return

    cl.user_session.set("resume_profile", profile)
    lines = ["Резюме учтено ✅"]
    if profile.headline:
        lines.append(f"- Профиль: **{profile.headline}**")
    if profile.skills:
        lines.append(f"- Навыки: {', '.join(profile.skills[:12])}")

    job_ctx: VacancyContext | None = cl.user_session.get("job_context")
    if job_ctx and job_ctx.skills:
        gaps = analyze_gaps(job_ctx.skills, profile)
        cl.user_session.set("gap_analysis", gaps)
        lines.append(f"\n**Совпадение с вакансией: {gaps.coverage_pct:g}%**")
        if gaps.matched:
            lines.append(f"- ✅ Подтверждено опытом: {', '.join(gaps.matched)}")
        if gaps.weak_evidence:
            lines.append(
                f"- 🟡 Указано, но без примеров в опыте: {', '.join(gaps.weak_evidence)}"
            )
        if gaps.missing:
            lines.append(f"- ⚠️ Нет в резюме: {', '.join(gaps.missing)}")
        lines.append("\nИнтервью будет сделано упором на пробелы и слабо подтверждённое.")
    else:
        lines.append(
            "\nВопросы будут опираться на твой реальный опыт и проекты из резюме."
        )

    if not cl.user_session.get("level") and profile.level_guess:
        cl.user_session.set("level", profile.level_guess)

    await cl.Message(content="\n".join(lines)).send()
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

    # Если навыки уже определены по вакансии — не переспрашиваем их отдельным шагом.
    if cl.user_session.get("job_context") and cl.user_session.get("required_skills"):
        await _start_interview()
        return

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


def _repeat_topics_context(role: str | None, user_id: str) -> str:
    """Блок «повтори слабое»: вопросы, на которых кандидат проседал раньше."""
    if not role:
        return ""
    try:
        topics = recent_weak_topics(role, user_id)
    except Exception:
        log.exception("weak topics lookup failed (best-effort, ignored)")
        return ""
    if not topics:
        return ""
    lines = "\n".join(f"- {t}" for t in topics)
    return (
        "В прошлых сессиях кандидат слабо ответил на вопросы:\n"
        f"{lines}\n"
        "Включи 1–2 новых вопроса на те же темы (другая формулировка, не повтор), "
        "чтобы проверить, подтянул ли он их."
    )


async def _start_interview():
    role = cl.user_session.get("role")
    level = cl.user_session.get("level")
    interview_type = cl.user_session.get("interview_type")
    role_category: str = cl.user_session.get("role_category") or "tech"
    required_skills: list[str] = cl.user_session.get("required_skills") or []
    is_custom_role: bool = bool(cl.user_session.get("is_custom_role"))
    db = cl.user_session.get("questions_db")
    job_ctx: VacancyContext | None = cl.user_session.get("job_context")
    company: CompanyProfile | None = cl.user_session.get("company_profile")
    resume: ResumeProfile | None = cl.user_session.get("resume_profile")
    gaps: GapAnalysis | None = cl.user_session.get("gap_analysis")
    repeat_ctx = _repeat_topics_context(role, _current_user_id())
    job_context_text = "\n\n".join(
        part
        for part in (
            job_ctx.as_prompt_context() if job_ctx else "",
            company.as_prompt_context() if company else "",
            resume.as_prompt_context() if resume else "",
            gaps.as_prompt_context() if gaps else "",
            repeat_ctx,
            language_question_directive(cl.user_session.get("lang")),
            "Стиль интервьюера: "
            + question_style(
                cl.user_session.get("persona"), bool(cl.user_session.get("stress_mode"))
            )
            if (
                cl.user_session.get("persona") not in (None, DEFAULT_PERSONA)
                or cl.user_session.get("stress_mode")
            )
            else "",
        )
        if part
    )

    mode = cl.user_session.get("interview_mode")
    min_q, max_q = compute_bounds(level, len(required_skills), mode)
    cl.user_session.set("min_q", min_q)
    cl.user_session.set("max_q", max_q)

    stylized = (
        cl.user_session.get("persona") not in (None, DEFAULT_PERSONA)
        or bool(cl.user_session.get("stress_mode"))
        or cl.user_session.get("lang") == "en"
    )
    needs_llm = bool(required_skills) or is_custom_role or resume is not None or stylized or bool(repeat_ctx)
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
                job_context_text,
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
    vacancy_line = (
        f"- По вакансии: **{job_ctx.company}**\n" if job_ctx and job_ctx.company else ""
    )
    await cl.Message(
        content=(
            f"**Параметры интервью**\n\n"
            f"- Роль: **{role}**\n"
            f"{vacancy_line}"
            f"- Уровень: **{_level_display(level)}**\n"
            f"- Тип: **{INTERVIEW_TYPES[interview_type]}**\n"
            f"- Интервьюер: **{get_persona(cl.user_session.get('persona')).label}**"
            f"{' · 🔥 стресс-режим' if cl.user_session.get('stress_mode') else ''}\n"
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


@cl.action_callback("practice")
async def on_practice(action: cl.Action):
    if cl.user_session.get("state") not in ("finished", "await_practice"):
        return
    kind = action.payload.get("value", "code")
    if kind not in PRACTICE_KINDS:
        return

    role = cl.user_session.get("role") or ""
    level = cl.user_session.get("level") or ""
    skills: list[str] = cl.user_session.get("required_skills") or []
    company: CompanyProfile | None = cl.user_session.get("company_profile")
    job_ctx: VacancyContext | None = cl.user_session.get("job_context")
    context = "\n\n".join(
        part
        for part in (
            job_ctx.as_prompt_context() if job_ctx else "",
            company.as_prompt_context() if company else "",
        )
        if part
    )
    try:
        async with cl.Step(name=f"Готовлю задание: {PRACTICE_KINDS[kind]}…", type="tool") as step:
            task = await asyncio.to_thread(generate_task, kind, role, level, skills, context)
            step.output = task.title
    except PracticeError as exc:
        await cl.Message(content=f"⚠️ Не получилось подготовить задание: {exc}").send()
        return

    cl.user_session.set("practice_task", task)
    cl.user_session.set("state", "await_practice")
    await cl.Message(content=format_task(task)).send()


async def _handle_practice_solution(text: str):
    task: PracticeTask | None = cl.user_session.get("practice_task")
    if task is None:
        cl.user_session.set("state", "finished")
        return
    level = cl.user_session.get("level") or ""
    try:
        async with cl.Step(name="Разбираю решение…", type="tool") as step:
            evaluation = await asyncio.to_thread(evaluate_solution, task, text, level)
            step.output = f"{evaluation.score}/10, критериев выполнено: {evaluation.met_count}/{len(evaluation.criteria)}"
    except PracticeError as exc:
        await cl.Message(
            content=f"⚠️ {exc}. Пришли решение ещё раз или нажми «Начать новое интервью»."
        ).send()
        return

    cl.user_session.set("practice_task", None)
    cl.user_session.set("state", "finished")
    await cl.Message(
        content=format_evaluation(evaluation), actions=make_report_actions()
    ).send()


@cl.action_callback("download_report")
async def on_download_report(action: cl.Action):
    markdown: str | None = cl.user_session.get("report_markdown")
    if not markdown:
        await cl.Message(content="Отчёт ещё не сформирован.").send()
        return
    session_id = (cl.user_session.get("session_id") or "report")[:8]
    await cl.Message(
        content="Отчёт готов — можно отправить ментору или сохранить:",
        elements=[
            cl.File(
                name=f"sokrat-report-{session_id}.md",
                content=markdown.encode("utf-8"),
                display="inline",
            )
        ],
    ).send()


@cl.action_callback("study_plan")
async def on_study_plan(action: cl.Action):
    focus_areas: list[str] = cl.user_session.get("focus_areas") or []
    if not focus_areas:
        await cl.Message(
            content="Слабых мест для плана не нашлось — результат уже сильный. "
            "Попробуй более сложный уровень или другую вакансию."
        ).send()
        return

    role = cl.user_session.get("role") or ""
    level = cl.user_session.get("level") or ""
    company: CompanyProfile | None = cl.user_session.get("company_profile")
    job_ctx: VacancyContext | None = cl.user_session.get("job_context")
    context = "\n\n".join(
        part
        for part in (
            job_ctx.as_prompt_context() if job_ctx else "",
            company.as_prompt_context() if company else "",
        )
        if part
    )
    try:
        async with cl.Step(name="Составляю план подготовки…", type="tool") as step:
            plan = await asyncio.to_thread(
                build_study_plan, role, level, int(action.payload.get("value", 7)),
                focus_areas, context,
            )
            step.output = f"дней: {len(plan)}"
    except StudyPlanError as exc:
        await cl.Message(content=f"⚠️ Не получилось составить план: {exc}").send()
        return

    await action.remove()
    await cl.Message(content=format_study_plan(plan), actions=make_restart_action()).send()


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

_MD_STRIP_RE = re.compile(r"[`*_>#]+")


async def _speak(text: str) -> None:
    """Озвучивает вопрос (если включён TTS). Best-effort: сбой не мешает интервью."""
    if not tts_enabled():
        return
    client = get_stt_client()
    if client is None:
        return
    clean = _MD_STRIP_RE.sub("", text).strip()
    try:
        audio = await asyncio.to_thread(client.synthesize_wav, clean)
    except SaluteSpeechError:
        log.warning("TTS failed (best-effort, ignored)", exc_info=True)
        return
    if not audio:
        return
    await cl.Message(
        content="",
        elements=[
            cl.Audio(name="question.wav", content=audio, mime="audio/wav", auto_play=True)
        ],
    ).send()


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

    await _speak(q["question"])
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

    star_result = None
    if (
        score_override > 0
        and analysis.intent == "answer"
        and is_behavioral(analysis.category, question.get("question", ""))
    ):
        star_result = await asyncio.to_thread(
            analyze_star,
            question.get("question", ""),
            answer_text,
            cl.user_session.get("level") or "",
            cl.user_session.get("role") or "",
        )

    answers.append(answer_text)
    scores.append(
        {
            "star": star_result.to_dict() if star_result else None,
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

    if star_result:
        await cl.Message(content=format_star_block(star_result.to_dict())).send()

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
    summary["star"] = aggregate_star(scores)
    dynamics = None
    try:
        dynamics = await asyncio.to_thread(
            build_dynamics, summary, role or "", session_id or "", None, _current_user_id()
        )
    except Exception:
        log.exception("dynamics build failed (best-effort, ignored)")
    gap_analysis: GapAnalysis | None = cl.user_session.get("gap_analysis")
    if cl.user_session.get("job_context") or gap_analysis:
        summary["readiness"] = compute_readiness(summary, gap_analysis)
    summary["dynamics"] = dynamics
    cl.user_session.set("summary", summary)
    cl.user_session.set(
        "focus_areas",
        collect_focus_areas(
            summary,
            gap_analysis.to_dict() if gap_analysis else None,
            weak_topics,
        ),
    )

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

    star_section = ""
    star_summary = summary.get("star")
    if star_summary:
        missed = star_summary.get("most_missed")
        missed_line = (
            f"\n- Чаще всего не хватает: **{STAR_LABELS[missed]}**" if missed in STAR_LABELS else ""
        )
        star_section = (
            "### 🧩 Поведенческие ответы (STAR)\n"
            f"- Полнота структуры: **{star_summary['avg_completeness_pct']:g}%** "
            f"(ответов: {star_summary['answers_count']}){missed_line}\n\n"
        )

    dynamics_section = format_dynamics(dynamics) if dynamics else ""
    job_for_compare: VacancyContext | None = cl.user_session.get("job_context")
    if job_for_compare and job_for_compare.company and _current_user_id():
        try:
            others = await asyncio.to_thread(
                other_vacancies, _current_user_id(), job_for_compare.company, session_id or ""
            )
        except Exception:
            log.exception("other vacancies lookup failed (best-effort, ignored)")
            others = []
        if others:
            dynamics_section += (
                "### 🆚 Готовность к другим вакансиям\n"
                + "\n".join(f"- **{o['company']}** ({o['role']}) — {o['readiness']}/100" for o in others)
                + "\n\n"
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
    readiness = summary.get("readiness")
    if readiness and summary["counted_count"] > 0:
        component_lines = "\n".join(
            f"  - {c['name']}: **{c['value']:g}%** _(вес {c['weight_pct']}%)_"
            for c in readiness["components"]
        )
        hero_block += (
            f"\n\n### 🎯 Индекс готовности к вакансии: **{readiness['index']}/100** — "
            f"{readiness['label']}\n{component_lines}"
        )
    cl.user_session.set("report_hero", hero_block)
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
        f"{star_section}"
        f"{dynamics_section}"
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
    if dynamics and len(dynamics["points"]) >= 2:
        progress_fig = _build_progress_figure(dynamics["points"])
        if progress_fig is not None:
            await cl.Message(
                content="### 📈 Прогресс по роли",
                elements=[
                    cl.Plotly(name="progress", figure=progress_fig, display="inline")
                ],
            ).send()

    cl.user_session.set(
        "report_markdown", f"# Сократ — отчёт по интервью\n\n{hero_block}\n\n{report}"
    )
    await cl.Message(content=report, actions=make_report_actions()).send()

    # Persist the session through the LangGraph save_session node.
    persist_state = await _run_graph(PHASE_PERSIST)
    if not persist_state.get("saved_path"):
        await cl.Message(
            content="⚠️ Не удалось сохранить сессию, но отчёт выше — твой результат.",
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
