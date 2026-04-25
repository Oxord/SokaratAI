import json
import os
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import chainlit as cl
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from sokrat.llm import get_chat_model
from sokrat.prompts import load_prompt

load_dotenv()

SESSION_DIR = Path(os.getenv("SESSION_DIR", "data/sessions"))
QUESTIONS_PATH = Path(os.getenv("QUESTIONS_PATH", "data/questions.json"))
TOTAL_QUESTIONS = 7

ROLES = ["Python Developer", "Frontend Developer", "Product Manager"]
LEVELS = ["Junior", "Middle", "Senior"]
INTERVIEW_TYPES = {
    "technical": "Техническое (стек, фреймворки, инструменты)",
    "hr": "HR / Поведенческое (STAR, мотивация, конфликты)",
    "mixed": "Смешанное (технические + HR вопросы)",
}

RECOMMENDATION_TEMPLATES = {
    "Strong": (
        "Отличный результат! Ты уверенно ответил на большинство вопросов. "
        "Ты хорошо подготовлен к этому уровню. "
        "Сосредоточься на паре слабых мест из списка выше — и будешь готов к реальному интервью."
    ),
    "Competent": (
        "Хорошая база. Ты понимаешь ключевые концепции, но есть области для роста. "
        "Проработай темы из раздела «Над чем поработать» — это значительно укрепит твою позицию. "
        "Продолжай практиковаться, и скоро выйдешь на уверенный уровень."
    ),
    "Needs Work": (
        "Есть над чем поработать. Не расстраивайся — это именно то, для чего и создан Сократ. "
        "Повтори фундаментальные концепции по темам из раздела «Над чем поработать». "
        "Попробуй пройти интервью ещё раз через несколько дней — результат улучшится."
    ),
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


def get_questions(db: dict, role: str, level: str, interview_type: str) -> list[dict]:
    try:
        pool = db["roles"][role][level][interview_type]
    except KeyError:
        pool = []

    if len(pool) < TOTAL_QUESTIONS:
        pool = pool + FALLBACK_QUESTIONS
        pool = pool[:TOTAL_QUESTIONS]

    selected = random.sample(pool, min(TOTAL_QUESTIONS, len(pool)))
    return selected


# ── LLM analysis ──────────────────────────────────────────────────────────────

ALLOWED_INTENTS = ("answer", "clarification", "dont_know", "skip", "meta")
ALLOWED_CATEGORIES = (
    "fundamentals", "system_design", "coding", "debugging",
    "soft_skills", "motivation", "behavioral", "other",
)


class AnswerEvaluation(BaseModel):
    intent: str
    score: Optional[int] = Field(default=None, ge=0, le=10)
    feedback: str
    explanation: Optional[str] = None
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    category: str = "other"


def _coerce_evaluation(value) -> AnswerEvaluation:
    if isinstance(value, AnswerEvaluation):
        result = value
    elif isinstance(value, dict):
        result = AnswerEvaluation.model_validate(value)
    else:
        raise ValueError(f"Unexpected analyzer output type: {type(value).__name__}")

    if result.intent not in ALLOWED_INTENTS:
        result.intent = "answer"
    if result.category not in ALLOWED_CATEGORIES:
        result.category = "other"
    return result


def analyze_answer_llm(
    role: str,
    level: str,
    interview_type: str,
    question: str,
    hints: list[str],
    answer: str,
    clarifications_used: int,
) -> AnswerEvaluation:
    template = load_prompt("analyzer")
    hints_text = ", ".join(hints) if hints else "(подсказок нет)"
    prompt = template.format(
        role=role,
        level=level,
        interview_type=interview_type,
        question=question,
        hints=hints_text,
        answer=answer,
        clarifications_used=clarifications_used,
    )

    model = get_chat_model(temperature=0.3)
    structured = model.with_structured_output(AnswerEvaluation, method="json_mode")

    try:
        return _coerce_evaluation(structured.invoke([HumanMessage(content=prompt)]))
    except (ValidationError, ValueError):
        retry_messages = [
            SystemMessage(
                content=(
                    "ВЕРНИ СТРОГО валидный JSON-объект, "
                    "полностью соответствующий схеме, "
                    "без markdown, без префиксов, без пояснений."
                )
            ),
            HumanMessage(content=prompt),
        ]
        return _coerce_evaluation(structured.invoke(retry_messages))


# ── Mock summary (BACKEND INTEGRATION POINT) ─────────────────────────────────
# Replace this function body with a call to the LangGraph graph:
#   result = await graph.ainvoke({"node": "summary", "answers": answers, "scores": scores, ...})
#   return result["summary"]

def mock_generate_summary(
    role: str,
    level: str,
    interview_type: str,
    questions: list[dict],
    answers: list[str],
    scores: list[dict],
) -> dict:
    counted = [s["score"] for s in scores if s["score"] > 0]
    skipped_count = sum(1 for s in scores if s["score"] == 0)
    total = sum(counted)
    max_possible = len(counted) * 10
    avg = total / len(counted) if counted else 0
    percentage = round(avg / 10 * 100, 1) if counted else 0.0

    if not counted:
        verdict = "Needs Work"
    elif avg >= 8.0:
        verdict = "Strong"
    elif avg >= 6.5:
        verdict = "Competent"
    else:
        verdict = "Needs Work"

    strengths = [
        questions[i]["question"]
        for i, s in enumerate(scores)
        if s["score"] >= 8 and i < len(questions)
    ]
    improvements = [
        questions[i]["question"]
        for i, s in enumerate(scores)
        if 0 < s["score"] <= 6 and i < len(questions)
    ]

    return {
        "avg_score": round(avg, 1),
        "total_score": total,
        "max_possible": max_possible,
        "percentage": percentage,
        "verdict": verdict,
        "strengths": strengths,
        "improvements": improvements,
        "skipped_count": skipped_count,
        "counted_count": len(counted),
        "recommendation": RECOMMENDATION_TEMPLATES[verdict],
        "per_question": scores,
    }


# ── Session persistence ───────────────────────────────────────────────────────

def save_session(session_id: str, data: dict) -> Path | None:
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path = SESSION_DIR / f"{session_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path
    except OSError:
        return None


# ── Button factories ──────────────────────────────────────────────────────────

def make_role_actions() -> list[cl.Action]:
    return [
        cl.Action(name="select_role", label=role, payload={"value": role})
        for role in ROLES
    ]


def make_level_actions() -> list[cl.Action]:
    return [
        cl.Action(name="select_level", label=level, payload={"value": level})
        for level in LEVELS
    ]


def make_type_actions() -> list[cl.Action]:
    return [
        cl.Action(name="select_type", label=label, payload={"value": key})
        for key, label in INTERVIEW_TYPES.items()
    ]


def make_restart_action() -> list[cl.Action]:
    return [
        cl.Action(
            name="restart",
            label="Начать новое интервью",
            payload={"value": "restart"},
        )
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _init_session():
    cl.user_session.set("state", "select_role")
    cl.user_session.set("session_id", str(uuid.uuid4()))
    cl.user_session.set("role", None)
    cl.user_session.set("level", None)
    cl.user_session.set("interview_type", None)
    cl.user_session.set("question_num", 0)
    cl.user_session.set("questions", [])
    cl.user_session.set("answers", [])
    cl.user_session.set("scores", [])
    cl.user_session.set("questions_db", load_questions_db())
    cl.user_session.set("clarifications_used", 0)
    cl.user_session.set("pending_dont_know", False)


TYPE_LABEL = {"technical": "Техническое", "hr": "HR / Поведенческое", "mixed": "Смешанное"}


def verdict_for_score(score: int) -> tuple[str, str, str]:
    """Returns (verdict_key, emoji, label) based on numeric score."""
    if score == 0:
        return "skipped", "⏭️", "Пропущено"
    if score >= 8:
        return "excellent", "✅", "Отлично"
    if score >= 6:
        return "good", "👍", "Хорошо"
    return "needs_work", "⚠️", "Можно лучше"


# ── Chainlit handlers ─────────────────────────────────────────────────────────

@cl.on_chat_start
async def on_chat_start():
    _init_session()

    await cl.Message(
        content=(
            "## Привет! Я **Сократ** 👋\n\n"
            "Твой AI-тренер для подготовки к собеседованиям.\n\n"
            "Я проведу mock-интервью из **7 вопросов**, дам мгновенный фидбэк "
            "после каждого ответа и сформирую итоговый отчёт с рекомендациями.\n\n"
            "---\n\n"
            "**С чего начнём?** Выбери целевую роль:"
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
    else:
        # State is select_role / select_level / select_type — resend the relevant buttons
        await _resend_selection_prompt(state)


async def _resend_selection_prompt(state: str):
    if state == "select_role":
        await cl.Message(
            content="Пожалуйста, **выбери роль** с помощью кнопок:",
            actions=make_role_actions(),
        ).send()
    elif state == "select_level":
        await cl.Message(
            content="Пожалуйста, **выбери уровень** с помощью кнопок:",
            actions=make_level_actions(),
        ).send()
    elif state == "select_type":
        await cl.Message(
            content="Пожалуйста, **выбери тип интервью** с помощью кнопок:",
            actions=make_type_actions(),
        ).send()


@cl.action_callback("select_role")
async def on_select_role(action: cl.Action):
    value = action.payload["value"]
    cl.user_session.set("role", value)
    cl.user_session.set("state", "select_level")
    await action.remove()
    await cl.Message(
        content=f"Роль: **{value}** ✓\n\nТеперь выбери уровень опыта:",
        actions=make_level_actions(),
    ).send()


@cl.action_callback("select_level")
async def on_select_level(action: cl.Action):
    value = action.payload["value"]
    cl.user_session.set("level", value)
    cl.user_session.set("state", "select_type")
    await action.remove()
    await cl.Message(
        content=f"Уровень: **{value}** ✓\n\nВыбери тип интервью:",
        actions=make_type_actions(),
    ).send()


@cl.action_callback("select_type")
async def on_select_type(action: cl.Action):
    role = cl.user_session.get("role")
    level = cl.user_session.get("level")
    interview_type = action.payload["value"]
    db = cl.user_session.get("questions_db")

    cl.user_session.set("interview_type", interview_type)
    questions = get_questions(db, role, level, interview_type)
    cl.user_session.set("questions", questions)
    cl.user_session.set("question_num", 0)
    cl.user_session.set("answers", [])
    cl.user_session.set("scores", [])
    cl.user_session.set("state", "in_interview")

    await action.remove()
    await cl.Message(
        content=(
            f"**Параметры интервью**\n\n"
            f"- Роль: **{role}**\n"
            f"- Уровень: **{level}**\n"
            f"- Тип: **{INTERVIEW_TYPES[interview_type]}**\n\n"
            f"Будет **{TOTAL_QUESTIONS} вопросов**. После каждого ответа — мгновенный фидбэк.\n\n"
            "Отвечай развёрнуто, как на настоящем интервью. Поехали! 🚀"
        )
    ).send()

    await _ask_next_question()


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

    if question_num >= len(questions):
        await _finish_interview()
        return

    q = questions[question_num]
    interview_type = cl.user_session.get("interview_type", "")
    type_tag = TYPE_LABEL.get(interview_type, interview_type)

    await cl.Message(
        content=(
            f"**Вопрос {question_num + 1} из {TOTAL_QUESTIONS}** _{type_tag}_\n\n"
            f"{q['question']}"
        )
    ).send()


async def _handle_answer(answer_text: str):
    question_num = cl.user_session.get("question_num")
    questions = cl.user_session.get("questions")

    if question_num >= len(questions):
        return

    question = questions[question_num]
    role = cl.user_session.get("role")
    level = cl.user_session.get("level")
    interview_type = cl.user_session.get("interview_type")
    clarifications_used = cl.user_session.get("clarifications_used") or 0

    try:
        async with cl.Step(name="Анализ ответа", type="tool") as step:
            analysis = analyze_answer_llm(
                role=role,
                level=level,
                interview_type=interview_type,
                question=question["question"],
                hints=question.get("hints", []),
                answer=answer_text,
                clarifications_used=clarifications_used,
            )
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
        await _handle_clarification(analysis, clarifications_used)
        return

    if analysis.intent == "dont_know":
        if cl.user_session.get("pending_dont_know"):
            await _record_and_advance(question, answer_text, analysis, score_override=0)
            return
        await _handle_dont_know(analysis)
        return

    if analysis.intent == "meta":
        await cl.Message(content=analysis.feedback).send()
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


async def _handle_dont_know(analysis: AnswerEvaluation):
    cl.user_session.set("pending_dont_know", True)
    await cl.Message(content=analysis.feedback).send()


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
            "question_id": question.get("id", ""),
        }
    )

    cl.user_session.set("answers", answers)
    cl.user_session.set("scores", scores)
    cl.user_session.set("question_num", question_num + 1)
    cl.user_session.set("clarifications_used", 0)
    cl.user_session.set("pending_dont_know", False)

    if score_override == 0:
        message = f"{emoji} **{label}**\n\n{analysis.feedback}"
    else:
        message = f"{emoji} **{label}** — {score_override}/10\n\n{analysis.feedback}"
    await cl.Message(content=message).send()

    await _ask_next_question()


async def _finish_interview():
    cl.user_session.set("state", "finished")

    role = cl.user_session.get("role")
    level = cl.user_session.get("level")
    interview_type = cl.user_session.get("interview_type")
    questions = cl.user_session.get("questions")
    answers = cl.user_session.get("answers")
    scores = cl.user_session.get("scores")
    session_id = cl.user_session.get("session_id")

    await cl.Message(content="Интервью завершено! Формирую отчёт...").send()

    async with cl.Step(name="Генерация итогового отчёта", type="tool") as step:
        summary = mock_generate_summary(
            role, level, interview_type, questions, answers, scores
        )
        step.output = (
            f"Итог: {summary['total_score']}/{summary['max_possible']} "
            f"({summary['percentage']}%) — {summary['verdict']}"
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
        if s["score"] == 0:
            per_q_lines.append(f"{i}. {e} пропущено — {q['question'][:60]}...")
        else:
            per_q_lines.append(f"{i}. {e} {s['score']}/10 — {q['question'][:60]}...")

    per_q_text = "\n".join(per_q_lines)

    if summary["counted_count"] == 0:
        result_line = "Все вопросы пропущены — попробуй ещё раз и отвечай хоть как-нибудь."
    else:
        result_line = (
            f"### Общий результат: {summary['total_score']}/{summary['max_possible']} "
            f"({summary['percentage']}%) — **{summary['verdict']}**"
        )
        if summary["skipped_count"] > 0:
            result_line += f"\n\n_Учтено {summary['counted_count']} ответов из {len(scores)} (пропущено: {summary['skipped_count']})_"

    report = (
        f"## Итоговый отчёт по интервью\n\n"
        f"**Роль:** {role} | **Уровень:** {level} | **Тип:** {type_label}\n\n"
        f"---\n\n"
        f"{result_line}\n\n"
        f"### Сильные стороны\n{strengths_text}\n\n"
        f"### Над чем поработать\n{improvements_text}\n\n"
        f"### По вопросам\n{per_q_text}\n\n"
        f"---\n\n"
        f"### Рекомендация\n{summary['recommendation']}\n\n"
        f"---\n\n"
        f"_Session ID: `{session_id}`_"
    )

    await cl.Message(content=report).send()

    # Build and save session JSON
    qa_pairs = [
        {
            "question_num": i + 1,
            "question_id": q.get("id", ""),
            "question_text": q["question"],
            "answer": answers[i] if i < len(answers) else "",
            "score": scores[i]["score"],
            "verdict": scores[i].get("verdict", ""),
            "feedback": scores[i]["feedback"],
            "intent": scores[i].get("intent", "answer"),
            "strengths": scores[i].get("strengths", []),
            "weaknesses": scores[i].get("weaknesses", []),
            "category": scores[i].get("category", "other"),
        }
        for i, q in enumerate(questions)
        if i < len(scores)
    ]

    session_data = {
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "level": level,
        "interview_type": interview_type,
        "summary": {
            "avg_score": summary["avg_score"],
            "total_score": summary["total_score"],
            "max_possible": summary["max_possible"],
            "percentage": summary["percentage"],
            "verdict": summary["verdict"],
            "strengths": summary["strengths"],
            "improvements": summary["improvements"],
            "recommendation": summary["recommendation"],
        },
        "qa_pairs": qa_pairs,
    }

    saved_path = save_session(session_id, session_data)
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
