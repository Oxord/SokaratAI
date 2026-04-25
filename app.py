import json
import os
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

import chainlit as cl
from dotenv import load_dotenv

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

FEEDBACK_TEMPLATES = {
    "excellent": [
        "Отличный ответ! Ты чётко раскрыл ключевые концепции и дал конкретные примеры.",
        "Превосходно. Твоё объяснение демонстрирует глубокое понимание темы.",
        "Очень полно. Ты охватил все важные аспекты и добавил полезный контекст.",
    ],
    "good": [
        "Хороший ответ. Ты затронул основные моменты, хотя можно было раскрыть подробнее.",
        "Крепкий ответ. Ты показал понимание сути вопроса.",
        "Неплохо. В следующий раз попробуй добавить конкретный пример — это усилит ответ.",
    ],
    "needs_work": [
        "Ответ требует большей глубины. Постарайся использовать конкретные термины и примеры.",
        "Ты затронул тему, но объяснение неполное. Попробуй структурировать ответ: определение → пример → вывод.",
        "Попробуй выстроить ответ чётче: сначала суть концепции, затем пример из практики.",
    ],
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


# ── Mock analysis (BACKEND INTEGRATION POINT) ────────────────────────────────
# Replace this function body with a call to the LangGraph graph:
#   result = await graph.ainvoke({"node": "analyze_answer", "question": question, "answer": answer, ...})
#   return result["analysis"]

def mock_analyze_answer(question: dict, answer: str) -> dict:
    answer_lower = answer.lower()
    matched = [kw for kw in question.get("ideal_keywords", []) if kw.lower() in answer_lower]
    keyword_score = min(len(matched), 5)
    base_score = 5 + keyword_score
    jitter = random.choice([-1, 0, 0, 1])
    final_score = max(5, min(10, base_score + jitter))

    if final_score >= 8:
        verdict = "excellent"
    elif final_score >= 6:
        verdict = "good"
    else:
        verdict = "needs_work"

    feedback = random.choice(FEEDBACK_TEMPLATES[verdict])

    hints = question.get("hints", [])
    if not matched and hints:
        tip = f"💡 Подсказка: подумай о **{hints[0]}**"
        if len(hints) > 1:
            tip += f" и **{hints[1]}**"
        feedback = tip + ".\n\n" + feedback

    return {
        "score": final_score,
        "verdict": verdict,
        "feedback": feedback,
        "matched_keywords": matched,
        "question_id": question.get("id", ""),
    }


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
    score_values = [s["score"] for s in scores]
    total = sum(score_values)
    max_possible = TOTAL_QUESTIONS * 10
    avg = total / len(score_values) if score_values else 0
    percentage = round(avg / 10 * 100, 1)

    if avg >= 8.0:
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
        if s["score"] <= 6 and i < len(questions)
    ]

    return {
        "avg_score": round(avg, 1),
        "total_score": total,
        "max_possible": max_possible,
        "percentage": percentage,
        "verdict": verdict,
        "strengths": strengths,
        "improvements": improvements,
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


VERDICT_EMOJI = {"excellent": "✅", "good": "👍", "needs_work": "⚠️"}
VERDICT_LABEL = {"excellent": "Отлично", "good": "Хорошо", "needs_work": "Можно лучше"}
TYPE_LABEL = {"technical": "Техническое", "hr": "HR / Поведенческое", "mixed": "Смешанное"}


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
    if len(answer_text.strip()) < 10:
        await cl.Message(
            content=(
                "Ответ слишком короткий. Пожалуйста, дай развёрнутый ответ — "
                "расскажи, что ты знаешь по этой теме."
            )
        ).send()
        return

    question_num = cl.user_session.get("question_num")
    questions = cl.user_session.get("questions")
    answers: list = cl.user_session.get("answers")
    scores: list = cl.user_session.get("scores")

    if question_num >= len(questions):
        return

    question = questions[question_num]
    answers.append(answer_text)

    async with cl.Step(name="Анализ ответа", type="tool") as step:
        analysis = mock_analyze_answer(question, answer_text)
        emoji = VERDICT_EMOJI[analysis["verdict"]]
        label = VERDICT_LABEL[analysis["verdict"]]
        step.output = f"{emoji} {label} · {analysis['score']}/10"

    scores.append(
        {
            "score": analysis["score"],
            "verdict": analysis["verdict"],
            "feedback": analysis["feedback"],
            "matched_keywords": analysis["matched_keywords"],
            "question_id": analysis["question_id"],
        }
    )

    cl.user_session.set("answers", answers)
    cl.user_session.set("scores", scores)
    cl.user_session.set("question_num", question_num + 1)

    emoji = VERDICT_EMOJI[analysis["verdict"]]
    label = VERDICT_LABEL[analysis["verdict"]]
    await cl.Message(
        content=(
            f"{emoji} **{label}** — {analysis['score']}/10\n\n"
            f"{analysis['feedback']}"
        )
    ).send()

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
        e = VERDICT_EMOJI[s["verdict"]]
        per_q_lines.append(f"{i}. {e} {s['score']}/10 — {q['question'][:60]}...")

    per_q_text = "\n".join(per_q_lines)

    report = (
        f"## Итоговый отчёт по интервью\n\n"
        f"**Роль:** {role} | **Уровень:** {level} | **Тип:** {type_label}\n\n"
        f"---\n\n"
        f"### Общий результат: {summary['total_score']}/{summary['max_possible']} "
        f"({summary['percentage']}%) — **{summary['verdict']}**\n\n"
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
            "verdict": scores[i]["verdict"],
            "feedback": scores[i]["feedback"],
            "matched_keywords": scores[i].get("matched_keywords", []),
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
