"""Движок интервью без привязки к UI (используется веб-интерфейсом).

Хранит живые сессии в памяти, использует те же модули, что и Chainlit-версия:
парсеры вакансии/резюме, профиль компании, gap-анализ, анализатор ответов, STAR,
индекс готовности, динамику, план подготовки и практику. Завершённые сессии
сохраняются в SESSIONS_DIR в том же формате, что читают history и analytics.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .analyzer import DIMENSION_LABELS, analyze_answer_llm, filter_skills_touched, prompt_hash
from .company_profile import CompanyProfile, build_company_profile
from .gap_analysis import GapAnalysis, analyze_gaps
from .history import build_dynamics, other_vacancies, recent_weak_topics
from .interview_rules import (
    INTERVIEW_MODES,
    INTERVIEW_TYPES,
    LEVELS,
    TYPE_LABEL,
    _MAX_ROLE_LEN,
    _extract_weak_topics,
    _pace_score,
    _parse_skills,
    compute_bounds,
    get_personalized_questions,
    load_questions_db,
    should_stop_interview,
    verdict_for_score,
)
from .llm import model_name_for
from .nodes.save_session import save_session_node
from .personas import (
    DEFAULT_PERSONA,
    PERSONAS,
    STRESS_TIME_LIMIT_SEC,
    feedback_style,
    language_feedback_directive,
    language_question_directive,
    question_style,
    suggest_persona,
)
from .practice import (
    KINDS as PRACTICE_KINDS,
    PracticeTask,
    evaluate_solution,
    generate_task,
)
from .readiness import compute_readiness
from .resource_generator import generate_resources
from .resume_parser import ResumeProfile, extract_resume_text, parse_resume
from .role_classifier import classify_role
from .scoring import compute_score
from .star_analysis import aggregate_star, analyze_star, is_behavioral
from .study_plan import build_study_plan, collect_focus_areas
from .summary_builder import compute_summary
from .vacancy_parser import VacancyContext, extract_vacancy_context

log = logging.getLogger(__name__)

PRESET_ROLES = ["Python Developer", "Frontend Developer", "Product Manager"]
_SESSION_TTL_SECONDS = 6 * 3600
_MAX_ANSWER_CHARS = 6000
_REPORT_QUESTION_CHARS = 400


def judge_points(question: dict) -> dict[str, list[str] | None]:
    """Что передать судье: фиксированные key_points вопроса, если есть, плюс ideal_keywords
    как нестрогие подсказки темы. Без key_points судья составляет пункты сам, как раньше."""
    return {
        "fixed_points": list(question.get("key_points") or []) or None,
        "bonus_points": list(question.get("bonus_points") or []) or None,
        "key_points": list(question.get("ideal_keywords") or []),
    }


class EngineError(RuntimeError):
    """Ошибка, понятная пользователю (показывается в интерфейсе как есть)."""


@dataclass
class Interview:
    id: str
    user_id: str = ""
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)
    step: str = "setup"  # setup | interview | finished

    role: str = ""
    role_category: str = "tech"
    is_custom_role: bool = False
    level: str = ""
    interview_type: str = ""
    mode: str = "medium"
    required_skills: list[str] = field(default_factory=list)
    persona: str = DEFAULT_PERSONA
    stress: bool = False
    lang: str = "ru"

    vacancy: VacancyContext | None = None
    company: CompanyProfile | None = None
    resume: ResumeProfile | None = None
    gaps: GapAnalysis | None = None

    questions: list[dict] = field(default_factory=list)
    question_num: int = 0
    answers: list[str] = field(default_factory=list)
    scores: list[dict] = field(default_factory=list)
    min_q: int = 0
    max_q: int = 0
    clarifications_used: int = 0
    pending_dont_know: bool = False
    question_started_at: float | None = None
    started_at_iso: str = ""

    summary: dict[str, Any] | None = None
    focus_areas: list[str] = field(default_factory=list)
    practice_task: PracticeTask | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class Engine:
    def __init__(self) -> None:
        self._sessions: dict[str, Interview] = {}
        self._guard = threading.Lock()

    # ── жизненный цикл ──────────────────────────────────────────────────────

    def create(self, user_id: str = "") -> Interview:
        self._evict_expired()
        iv = Interview(id=str(uuid.uuid4()), user_id=user_id)
        with self._guard:
            self._sessions[iv.id] = iv
        return iv

    def get(self, interview_id: str, user_id: str = "") -> Interview:
        with self._guard:
            iv = self._sessions.get(interview_id)
        if iv is None:
            raise EngineError("Сессия не найдена или истекла — начните заново.")
        if iv.user_id and iv.user_id != user_id:
            raise EngineError("Нет доступа к этой сессии.")
        iv.touched_at = time.time()
        return iv

    def _evict_expired(self) -> None:
        cutoff = time.time() - _SESSION_TTL_SECONDS
        with self._guard:
            for key in [k for k, v in self._sessions.items() if v.touched_at < cutoff]:
                del self._sessions[key]

    # ── подготовка ──────────────────────────────────────────────────────────

    def set_role(self, iv: Interview, role: str, custom: bool) -> dict[str, Any]:
        self._require_step(iv, "setup")
        role = (role or "").strip()[:_MAX_ROLE_LEN]
        if len(role) < 3:
            raise EngineError("Название должности слишком короткое (минимум 3 символа).")
        iv.role = role
        iv.is_custom_role = custom or role not in PRESET_ROLES
        iv.role_category = classify_role(role) if iv.is_custom_role else "tech"
        return self.setup_view(iv)

    def load_vacancy(self, iv: Interview, text: str) -> dict[str, Any]:
        self._require_step(iv, "setup")
        raw = (text or "").strip()
        if len(raw) < 10:
            raise EngineError("Вставь ссылку на вакансию или её текст.")
        try:
            ctx = extract_vacancy_context(raw)
        except Exception as exc:  # VacancyParseError и сетевые сбои
            raise EngineError(f"Не удалось разобрать вакансию: {exc}") from exc

        iv.vacancy = ctx
        iv.role = ctx.title or raw[:_MAX_ROLE_LEN]
        iv.is_custom_role = True
        iv.role_category = classify_role(iv.role)
        if ctx.level_guess:
            iv.level = ctx.level_guess
        if ctx.interview_type_guess:
            iv.interview_type = ctx.interview_type_guess
        if ctx.skills:
            iv.required_skills = list(ctx.skills)

        iv.company = None
        if ctx.company:
            profile = build_company_profile(ctx.company, ctx)
            if not profile.is_empty():
                iv.company = profile
                suggested = suggest_persona(profile.industry, ctx.company_description)
                if suggested != DEFAULT_PERSONA:
                    iv.persona = suggested
        self._recompute_gaps(iv)
        return self.setup_view(iv)

    def load_resume(self, iv: Interview, file_path: str) -> dict[str, Any]:
        self._require_step(iv, "setup")
        try:
            text = extract_resume_text(file_path)
            iv.resume = parse_resume(text)
        except Exception as exc:  # ResumeParseError
            raise EngineError(f"Не получилось разобрать резюме: {exc}") from exc
        if not iv.level and iv.resume.level_guess:
            iv.level = iv.resume.level_guess
        self._recompute_gaps(iv)
        return self.setup_view(iv)

    def clear_resume(self, iv: Interview) -> dict[str, Any]:
        self._require_step(iv, "setup")
        iv.resume = None
        iv.gaps = None
        return self.setup_view(iv)

    def set_params(self, iv: Interview, params: dict[str, Any]) -> dict[str, Any]:
        self._require_step(iv, "setup")
        level = params.get("level")
        if level is not None:
            if level not in LEVELS:
                raise EngineError("Неизвестный уровень.")
            iv.level = level
        interview_type = params.get("interview_type")
        if interview_type is not None:
            if interview_type not in INTERVIEW_TYPES:
                raise EngineError("Неизвестный тип интервью.")
            iv.interview_type = interview_type
        mode = params.get("mode")
        if mode is not None:
            if mode not in INTERVIEW_MODES:
                raise EngineError("Неизвестный режим.")
            iv.mode = mode
        persona = params.get("persona")
        if persona is not None:
            if persona not in PERSONAS:
                raise EngineError("Неизвестная персона интервьюера.")
            iv.persona = persona
        if "stress" in params:
            iv.stress = bool(params["stress"])
        lang = params.get("lang")
        if lang is not None:
            iv.lang = "en" if lang == "en" else "ru"
        if "skills" in params:
            skills = params["skills"]
            iv.required_skills = (
                _parse_skills(skills) if isinstance(skills, str)
                else _parse_skills(",".join(str(s) for s in skills or []))
            )
            self._recompute_gaps(iv)
        return self.setup_view(iv)

    def _recompute_gaps(self, iv: Interview) -> None:
        skills = (iv.vacancy.skills if iv.vacancy else []) or []
        iv.gaps = analyze_gaps(skills, iv.resume) if (iv.resume and skills) else None

    # ── интервью ────────────────────────────────────────────────────────────

    def start(self, iv: Interview) -> dict[str, Any]:
        self._require_step(iv, "setup")
        if not iv.role:
            raise EngineError("Сначала выбери должность или вставь вакансию.")
        if not iv.level:
            raise EngineError("Выбери уровень.")
        if not iv.interview_type:
            raise EngineError("Выбери тип интервью.")

        iv.min_q, iv.max_q = compute_bounds(iv.level, len(iv.required_skills), iv.mode)
        context = self._generation_context(iv)
        stylized = iv.persona != DEFAULT_PERSONA or iv.stress or iv.lang == "en"
        repeat_needed = bool(context.get("repeat"))

        db = load_questions_db()
        questions = get_personalized_questions(
            db,
            iv.role,
            iv.level,
            iv.interview_type,
            iv.required_skills,
            iv.is_custom_role or iv.resume is not None or stylized or repeat_needed,
            iv.max_q,
            iv.role_category,
            context["text"],
        )
        if not questions:
            raise EngineError("Не удалось подготовить вопросы. Попробуй ещё раз.")

        iv.questions = questions
        iv.question_num = 0
        iv.answers, iv.scores = [], []
        iv.clarifications_used = 0
        iv.pending_dont_know = False
        iv.started_at_iso = datetime.now(timezone.utc).isoformat()
        iv.step = "interview"
        iv.question_started_at = time.time()
        return {"question": self._question_view(iv), "setup": self.setup_view(iv)}

    def current_question(self, iv: Interview) -> dict[str, Any]:
        """Текущее состояние для восстановления страницы интервью после перезагрузки."""
        if iv.step == "interview" and iv.question_num < len(iv.questions):
            return {"finished": False, "question": self._question_view(iv)}
        if iv.step == "finished" or (iv.step == "interview" and iv.question_num >= len(iv.questions)):
            return {"finished": True, "question": None}
        raise EngineError("Интервью ещё не начато.")

    def answer(self, iv: Interview, text: str) -> dict[str, Any]:
        self._require_step(iv, "interview")
        answer_text = (text or "").strip()
        if not answer_text:
            raise EngineError("Напиши ответ или нажми «Пропустить».")
        answer_text = answer_text[:_MAX_ANSWER_CHARS]
        with iv.lock:
            return self._answer_locked(iv, answer_text)

    def skip(self, iv: Interview) -> dict[str, Any]:
        self._require_step(iv, "interview")
        with iv.lock:
            question = iv.questions[iv.question_num]
            return self._record(iv, question, "(пропущено)", None, 0, "skip")

    def _answer_locked(self, iv: Interview, answer_text: str) -> dict[str, Any]:
        question = iv.questions[iv.question_num]
        elapsed = self._elapsed(iv)
        clarifications = max(iv.clarifications_used, 2) if iv.stress else iv.clarifications_used
        persona_style = " ".join(
            p
            for p in (feedback_style(iv.persona, iv.stress), language_feedback_directive(iv.lang))
            if p
        )
        try:
            analysis = analyze_answer_llm(
                role=iv.role,
                level=iv.level,
                interview_type=iv.interview_type,
                question=question.get("question", ""),
                hints=question.get("hints", []) or [],
                **judge_points(question),
                answer=answer_text,
                clarifications_used=clarifications,
                required_skills=iv.required_skills,
                elapsed_seconds=elapsed,
                role_category=iv.role_category,
                persona_style=persona_style,
            )
            analysis.skills_touched = filter_skills_touched(
                analysis.skills_touched, iv.required_skills
            )
        except Exception as exc:
            log.exception("answer analysis failed")
            raise EngineError(
                "Что-то пошло не так с анализом ответа. Попробуй отправить ещё раз."
            ) from exc

        intent = analysis.intent
        if intent == "clarification":
            iv.clarifications_used += 1
            return {
                "kind": "clarification",
                "feedback": analysis.feedback,
                "explanation": analysis.explanation or "",
                "clarifications_left": max(0, 2 - iv.clarifications_used),
            }
        if intent == "dont_know":
            if iv.pending_dont_know:
                return self._record(iv, question, answer_text, analysis, 0, "dont_know")
            iv.pending_dont_know = True
            return {"kind": "dont_know", "feedback": analysis.feedback}
        if intent == "meta":
            return {"kind": "meta", "feedback": analysis.feedback}
        if intent == "skip":
            return self._record(iv, question, answer_text, analysis, 0, "skip")

        score = self._answer_score(analysis)
        return self._record(iv, question, answer_text, analysis, score, "answer")

    @staticmethod
    def _answer_score(analysis: Any) -> int:
        # 0 для answer промпт запрещает: summary посчитал бы такой ответ пропущенным.
        model_score = analysis.score or 1
        if getattr(analysis, "points_mismatch", False):
            # Статусы не привязаны к фиксированным пунктам — D по ним считать нельзя.
            log.warning("analyzer: points_mismatch, беру балл модели %s", model_score)
            return model_score
        computed = compute_score(
            analysis.points, analysis.errors, bonus=analysis.bonus, off_topic=analysis.off_topic,
            bonus_marks=getattr(analysis, "bonus_marks", None),
        )
        if computed is None:
            # Только структура, без цитат из ответа пользователя.
            log.warning(
                "analyzer: points/errors пустые или битые (points=%s, errors=%s), беру балл модели %s",
                [p.get("status") if isinstance(p, dict) else type(p).__name__ for p in analysis.points],
                [e.get("core") if isinstance(e, dict) else type(e).__name__ for e in analysis.errors],
                model_score,
            )
            return model_score
        return computed

    def _record(
        self,
        iv: Interview,
        question: dict,
        answer_text: str,
        analysis: Any,
        score: int,
        intent: str,
    ) -> dict[str, Any]:
        elapsed = self._elapsed(iv)
        pace = _pace_score(elapsed) if score > 0 else None
        _, _, label = verdict_for_score(score)

        star = None
        if score > 0 and intent == "answer" and analysis is not None and is_behavioral(
            analysis.category, question.get("question", "")
        ):
            result = analyze_star(
                question.get("question", ""), answer_text, iv.level, iv.role
            )
            star = result.to_dict() if result else None

        entry = {
            "star": star,
            "score": score,
            "verdict": label,
            "feedback": analysis.feedback if analysis else "",
            "strengths": list(analysis.strengths) if analysis else [],
            "weaknesses": list(analysis.weaknesses) if analysis else [],
            "category": analysis.category if analysis else "other",
            "intent": intent,
            "skills_touched": list(analysis.skills_touched or []) if analysis else [],
            "question_id": question.get("id", ""),
            "role_fit": analysis.role_fit if analysis else None,
            "structure": analysis.structure if analysis else None,
            "literacy": analysis.literacy if analysis else None,
            "oratory": analysis.oratory if analysis else None,
            "depth": analysis.depth if analysis else None,
            "pace": pace,
            "elapsed_seconds": round(elapsed, 1),
            "ideal_answer": analysis.ideal_answer if analysis else None,
            # Разбор судьи и происхождение оценки — для отладки и eval, в UI не выводятся.
            "score_model": analysis.score if analysis else None,
            "injection_suspected": analysis.injection_suspected if analysis else None,
            "off_topic": analysis.off_topic if analysis else None,
            "bonus": analysis.bonus if analysis else None,
            "expected_points": list(analysis.expected_points) if analysis else [],
            "points": list(analysis.points) if analysis else [],
            "errors": list(analysis.errors) if analysis else [],
            # Происхождение эталонных пунктов: "llm" — черновик, не проверенный человеком.
            "key_points_source": question.get("key_points_source") if question.get("key_points") else None,
            "points_mismatch": bool(getattr(analysis, "points_mismatch", False)) if analysis else False,
            "bonus_marks": list(getattr(analysis, "bonus_marks", None) or []) if analysis else [],
            "model": model_name_for(iv.role_category) if analysis else None,
            "prompt_hash": prompt_hash() if analysis else None,
        }
        iv.answers.append(answer_text)
        iv.scores.append(entry)
        iv.question_num += 1
        iv.clarifications_used = 0
        iv.pending_dont_know = False

        finished = should_stop_interview(
            iv.question_num, iv.min_q, iv.max_q, iv.scores, iv.required_skills
        ) or iv.question_num >= len(iv.questions)
        next_question = None
        if not finished:
            iv.question_started_at = time.time()
            next_question = self._question_view(iv)
        else:
            iv.question_started_at = None

        return {
            "kind": "scored",
            "score": score,
            "verdict": label,
            "verdict_key": verdict_for_score(score)[0],
            "feedback": entry["feedback"],
            "strengths": entry["strengths"],
            "weaknesses": entry["weaknesses"],
            "ideal_answer": entry["ideal_answer"] if score < 6 else None,
            "star": star,
            "elapsed_seconds": entry["elapsed_seconds"],
            "pace": pace,
            "finished": finished,
            "question": next_question,
        }

    def _generation_context(self, iv: Interview) -> dict[str, Any]:
        repeat = ""
        try:
            topics = recent_weak_topics(iv.role, iv.user_id)
        except Exception:
            log.exception("weak topics lookup failed (best-effort, ignored)")
            topics = []
        if topics:
            lines = "\n".join(f"- {t}" for t in topics)
            repeat = (
                "В прошлых сессиях кандидат слабо ответил на вопросы:\n"
                f"{lines}\n"
                "Включи 1–2 новых вопроса на те же темы (другая формулировка, не повтор), "
                "чтобы проверить, подтянул ли он их."
            )
        styled = (
            "Стиль интервьюера: " + question_style(iv.persona, iv.stress)
            if (iv.persona != DEFAULT_PERSONA or iv.stress)
            else ""
        )
        parts = (
            iv.vacancy.as_prompt_context() if iv.vacancy else "",
            iv.company.as_prompt_context() if iv.company else "",
            iv.resume.as_prompt_context() if iv.resume else "",
            iv.gaps.as_prompt_context() if iv.gaps else "",
            repeat,
            language_question_directive(iv.lang),
            styled,
        )
        return {"text": "\n\n".join(p for p in parts if p), "repeat": repeat}

    def _elapsed(self, iv: Interview) -> float:
        if iv.question_started_at is None:
            return 0.0
        return max(0.0, time.time() - iv.question_started_at)

    def _question_view(self, iv: Interview) -> dict[str, Any]:
        q = iv.questions[iv.question_num]
        return {
            "number": iv.question_num + 1,
            "max": iv.max_q,
            "min": iv.min_q,
            "text": q.get("question", ""),
            "type_label": TYPE_LABEL.get(iv.interview_type, iv.interview_type),
            "time_limit": STRESS_TIME_LIMIT_SEC if iv.stress else None,
            "clarifications_allowed": not iv.stress,
        }

    # ── итоги ───────────────────────────────────────────────────────────────

    def finish(self, iv: Interview) -> dict[str, Any]:
        if iv.step == "finished" and iv.summary is not None:
            return self.report_view(iv)
        if iv.step != "interview":
            raise EngineError("Интервью ещё не начато.")
        with iv.lock:
            if iv.step == "finished" and iv.summary is not None:
                return self.report_view(iv)
            iv.step = "finished"
            iv.question_started_at = None
            summary = compute_summary(
                iv.role, iv.level, iv.interview_type,
                iv.questions[: len(iv.scores)], iv.answers, iv.scores, iv.required_skills,
            )
            weak_topics = _extract_weak_topics(
                iv.scores, iv.questions, summary.get("skills_coverage") or []
            )
            resources: list[dict] = []
            if weak_topics:
                try:
                    resources = generate_resources(
                        iv.role, iv.level, weak_topics, iv.role_category
                    )
                except Exception:
                    log.exception("resource generation failed (best-effort, ignored)")
            summary["resources"] = resources
            summary["star"] = aggregate_star(iv.scores)
            try:
                summary["dynamics"] = build_dynamics(
                    summary, iv.role, iv.id, None, iv.user_id
                )
            except Exception:
                log.exception("dynamics build failed (best-effort, ignored)")
                summary["dynamics"] = None
            if iv.vacancy or iv.gaps:
                summary["readiness"] = compute_readiness(summary, iv.gaps)
            iv.summary = summary
            iv.focus_areas = collect_focus_areas(
                summary, iv.gaps.to_dict() if iv.gaps else None, weak_topics
            )
            self._persist(iv)
        return self.report_view(iv)

    def _persist(self, iv: Interview) -> None:
        state = {
            "session_id": iv.id,
            "user_id": iv.user_id,
            "started_at": iv.started_at_iso,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "role": iv.role,
            "role_category": iv.role_category,
            "level": iv.level,
            "interview_type": iv.interview_type,
            "interview_mode": iv.mode,
            "is_custom_role": iv.is_custom_role,
            "required_skills": iv.required_skills,
            "questions": iv.questions[: len(iv.scores)],
            "answers": iv.answers,
            "scores": iv.scores,
            "summary": iv.summary,
            "interviewer_persona": iv.persona,
            "stress_mode": iv.stress,
            "current_step": "done",
        }
        if iv.vacancy:
            state["job_vacancy"] = {
                "company": iv.vacancy.company,
                "source": iv.vacancy.source,
                "company_description": iv.vacancy.company_description,
                "title": iv.vacancy.title,
            }
        if iv.company:
            state["company_profile"] = iv.company.to_dict()
        if iv.resume:
            state["resume_profile"] = iv.resume.to_dict()
        if iv.gaps:
            state["gap_analysis"] = iv.gaps.to_dict()
        result = save_session_node(state)  # type: ignore[arg-type]
        if not result.get("saved_path"):
            log.error("session save failed: %s", result.get("errors"))

    def report_view(self, iv: Interview) -> dict[str, Any]:
        if iv.summary is None:
            raise EngineError("Отчёт ещё не готов.")
        other: list[dict[str, Any]] = []
        if iv.vacancy and iv.vacancy.company and iv.user_id:
            try:
                other = other_vacancies(iv.user_id, iv.vacancy.company, iv.id)
            except Exception:
                log.exception("other vacancies lookup failed (best-effort, ignored)")
        data = {
            "session_id": iv.id,
            "role": iv.role,
            "level": iv.level,
            "interview_type": iv.interview_type,
            "required_skills": iv.required_skills,
            "questions": iv.questions[: len(iv.scores)],
            "answers": iv.answers,
            "scores": iv.scores,
            "summary": iv.summary,
            "persona": iv.persona,
            "stress": iv.stress,
            "company": iv.vacancy.company if iv.vacancy else "",
            "company_profile": iv.company.to_dict() if iv.company else None,
            "gap_analysis": iv.gaps.to_dict() if iv.gaps else None,
        }
        view = build_report_view(data)
        view["other_vacancies"] = other
        view["can_plan"] = bool(iv.focus_areas)
        return view

    # ── план, практика ──────────────────────────────────────────────────────

    def study_plan(self, iv: Interview, days: int = 7) -> dict[str, Any]:
        if iv.summary is None:
            raise EngineError("План строится после завершения интервью.")
        if not iv.focus_areas:
            raise EngineError(
                "Слабых мест для плана не нашлось — результат уже сильный. "
                "Попробуй более сложный уровень или другую вакансию."
            )
        context = "\n\n".join(
            p
            for p in (
                iv.vacancy.as_prompt_context() if iv.vacancy else "",
                iv.company.as_prompt_context() if iv.company else "",
            )
            if p
        )
        try:
            plan = build_study_plan(iv.role, iv.level, days, iv.focus_areas, context)
        except Exception as exc:  # StudyPlanError
            raise EngineError(f"Не получилось составить план: {exc}") from exc
        return {
            "days": [
                {
                    "day": d.day,
                    "focus": d.focus,
                    "tasks": d.tasks,
                    "minutes": d.minutes,
                    "checkpoint": d.checkpoint,
                }
                for d in plan
            ],
            "total_minutes": sum(d.minutes for d in plan),
        }

    def practice_generate(self, iv: Interview, kind: str) -> dict[str, Any]:
        if iv.summary is None:
            raise EngineError("Практика доступна после завершения интервью.")
        if kind not in PRACTICE_KINDS:
            raise EngineError("Неизвестный тип задания.")
        context = "\n\n".join(
            p
            for p in (
                iv.vacancy.as_prompt_context() if iv.vacancy else "",
                iv.company.as_prompt_context() if iv.company else "",
            )
            if p
        )
        try:
            task = generate_task(kind, iv.role, iv.level, iv.required_skills, context)
        except Exception as exc:  # PracticeError
            raise EngineError(f"Не получилось подготовить задание: {exc}") from exc
        iv.practice_task = task
        return {
            "kind": task.kind,
            "kind_label": PRACTICE_KINDS[task.kind],
            "title": task.title,
            "statement": task.statement,
            "rubric": task.rubric,
            "hints": task.hints,
        }

    def practice_evaluate(self, iv: Interview, solution: str) -> dict[str, Any]:
        task = iv.practice_task
        if task is None:
            raise EngineError("Сначала получи задание.")
        try:
            ev = evaluate_solution(task, solution, iv.level)
        except Exception as exc:  # PracticeError
            raise EngineError(f"{exc}") from exc
        return {
            "score": ev.score,
            "summary": ev.summary,
            "criteria": ev.criteria,
            "improvement": ev.improvement,
            "met": ev.met_count,
        }

    # ── представления ───────────────────────────────────────────────────────

    def setup_view(self, iv: Interview) -> dict[str, Any]:
        v = iv.vacancy
        return {
            "id": iv.id,
            "step": iv.step,
            "role": iv.role,
            "level": iv.level,
            "interview_type": iv.interview_type,
            "mode": iv.mode,
            "persona": iv.persona,
            "stress": iv.stress,
            "lang": iv.lang,
            "skills": iv.required_skills,
            "vacancy": (
                {
                    "title": v.title,
                    "company": v.company,
                    "source": v.source,
                    "skills": v.skills,
                    "responsibilities": v.responsibilities,
                    "description": v.company_description,
                }
                if v
                else None
            ),
            "company": iv.company.to_dict() if iv.company else None,
            "resume": (
                {
                    "headline": iv.resume.headline,
                    "level_guess": iv.resume.level_guess,
                    "years": iv.resume.years_experience,
                    "skills": iv.resume.skills,
                }
                if iv.resume
                else None
            ),
            "gaps": iv.gaps.to_dict() if iv.gaps else None,
        }

    @staticmethod
    def _require_step(iv: Interview, step: str) -> None:
        if iv.step != step:
            raise EngineError(
                "Это действие сейчас недоступно: интервью уже идёт или завершено."
                if step == "setup"
                else "Интервью сейчас не активно."
            )


def build_report_view(data: dict[str, Any]) -> dict[str, Any]:
    """Структурированный отчёт из сохранённой/живой сессии (одинаков для обеих)."""
    summary = data.get("summary") or {}
    questions = data.get("questions") or []
    answers = data.get("answers") or []
    scores = data.get("scores") or []
    items = []
    for i, sc in enumerate(scores):
        question = questions[i] if i < len(questions) else {}
        items.append(
            {
                "number": i + 1,
                "question": str(question.get("question", ""))[:_REPORT_QUESTION_CHARS],
                "answer": answers[i] if i < len(answers) else "",
                "score": sc.get("score", 0),
                "verdict": sc.get("verdict", ""),
                "verdict_key": verdict_for_score(int(sc.get("score", 0)))[0],
                "feedback": sc.get("feedback", ""),
                "strengths": sc.get("strengths", []),
                "weaknesses": sc.get("weaknesses", []),
                "ideal_answer": sc.get("ideal_answer") if sc.get("score", 0) < 6 else None,
                "star": sc.get("star"),
                "elapsed_seconds": sc.get("elapsed_seconds"),
                "category": sc.get("category"),
            }
        )
    dims = summary.get("dimensions") or {}
    return {
        "session_id": data.get("session_id", ""),
        "role": data.get("role", ""),
        "level": data.get("level", ""),
        "interview_type": data.get("interview_type", ""),
        "type_label": TYPE_LABEL.get(data.get("interview_type", ""), data.get("interview_type", "")),
        "company": data.get("company") or (data.get("job_vacancy") or {}).get("company", ""),
        "persona": data.get("persona") or data.get("interviewer_persona") or DEFAULT_PERSONA,
        "stress": bool(data.get("stress") or data.get("stress_mode")),
        "required_skills": data.get("required_skills", []),
        "percentage": summary.get("percentage", 0),
        "verdict": summary.get("verdict", ""),
        "avg_score": summary.get("avg_score", 0),
        "total_score": summary.get("total_score", 0),
        "max_possible": summary.get("max_possible", 0),
        "counted": summary.get("counted_count", 0),
        "skipped": summary.get("skipped_count", 0),
        "recommendation": summary.get("recommendation", ""),
        "dimensions": [
            {"key": k, "label": DIMENSION_LABELS[k], "value": dims.get(k, 0.0)}
            for k in DIMENSION_LABELS
        ],
        "has_dimensions": any((dims.get(k) or 0) > 0 for k in DIMENSION_LABELS),
        "skills_coverage": summary.get("skills_coverage", []),
        "strengths": summary.get("strengths", []),
        "improvements": summary.get("improvements", []),
        "total_time_seconds": summary.get("total_time_seconds", 0),
        "avg_time_seconds": summary.get("avg_time_seconds", 0),
        "resources": summary.get("resources", []),
        "star": summary.get("star"),
        "readiness": summary.get("readiness"),
        "dynamics": summary.get("dynamics"),
        "company_profile": data.get("company_profile"),
        "gap_analysis": data.get("gap_analysis"),
        "items": items,
    }


def report_markdown(view: dict[str, Any]) -> str:
    lines = [
        "# Сократ — отчёт по интервью",
        "",
        f"**Роль:** {view['role']} · **Уровень:** {view['level']} · **Тип:** {view['type_label']}",
        "",
        f"## {view['verdict']} — {view['percentage']}%",
        f"_{view['total_score']}/{view['max_possible']} баллов_",
    ]
    readiness = view.get("readiness")
    if readiness:
        lines += ["", f"### Индекс готовности к вакансии: {readiness['index']}/100 — {readiness['label']}"]
        for c in readiness.get("components", []):
            lines.append(f"- {c['name']}: {c['value']:g}% (вес {c['weight_pct']}%)")
    if view["has_dimensions"]:
        lines += ["", "### Профиль интервью (0–10)"]
        lines += [f"- {d['label']}: {d['value']}" for d in view["dimensions"]]
    if view["skills_coverage"]:
        lines += ["", "### Покрытие навыков"]
        for e in view["skills_coverage"]:
            if e.get("questions_count"):
                lines.append(f"- {e['skill']}: {e['avg_score']}/10 (вопросов: {e['questions_count']})")
            else:
                lines.append(f"- {e['skill']}: не раскрыт")
    gap = view.get("gap_analysis")
    if gap:
        lines += ["", "### Резюме ↔ вакансия", f"Совпадение: {gap.get('coverage_pct', 0)}%"]
        if gap.get("missing"):
            lines.append(f"- Нет в резюме: {', '.join(gap['missing'])}")
        if gap.get("weak_evidence"):
            lines.append(f"- Без примеров опыта: {', '.join(gap['weak_evidence'])}")
    lines += ["", "### По вопросам"]
    for it in view["items"]:
        mark = "пропущено" if it["score"] == 0 else f"{it['score']}/10"
        lines += ["", f"**{it['number']}. {it['question']}** — {mark}"]
        if it["feedback"]:
            lines.append(it["feedback"])
    if view["recommendation"]:
        lines += ["", "### Рекомендация", view["recommendation"]]
    return "\n".join(lines) + "\n"


engine = Engine()

__all__ = [
    "Engine",
    "EngineError",
    "Interview",
    "PRESET_ROLES",
    "build_report_view",
    "engine",
    "report_markdown",
]
