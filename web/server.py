"""Веб-сервер Сократа: REST API + статический интерфейс (лендинг, мастер, интервью,
отчёт, личный кабинет).

Запуск:  uvicorn web.server:app --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sokrat.config import SESSIONS_DIR
from sokrat.engine import (
    PRESET_ROLES,
    EngineError,
    build_report_view,
    engine,
    report_markdown,
)
from sokrat.history import load_sessions, recent_weak_topics
from sokrat.interview_rules import (
    INTERVIEW_MODES,
    INTERVIEW_TYPES,
    LEVEL_LABELS,
    LEVELS,
)
from sokrat.llm import llm_configured
from sokrat.personas import PERSONAS
from sokrat.practice import KINDS as PRACTICE_KINDS
from sokrat.stt import SaluteSpeechError, get_stt_client, tts_enabled
from sokrat.users import register, validate_credentials, verify
from web.auth import COOKIE_NAME, SESSION_DAYS, LoginThrottle, make_token, read_token

log = logging.getLogger("sokrat.web")

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
PUBLIC_DIR = ROOT.parent / "public"

_MAX_RESUME_BYTES = 5 * 1024 * 1024
_MAX_AUDIO_BYTES = 2 * 1024 * 1024
_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
_SECURE_COOKIE = os.getenv("SOKRAT_COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

app = FastAPI(title="Sokrat", docs_url=None, redoc_url=None)
_throttle = LoginThrottle()


# ── служебное ────────────────────────────────────────────────────────────────

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(EngineError)
async def engine_error_handler(_: Request, exc: EngineError):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception):
    log.exception("unhandled error: %s", exc)
    return JSONResponse(
        {"error": "Внутренняя ошибка сервера. Попробуй ещё раз."}, status_code=500
    )


def current_user(request: Request) -> str:
    return read_token(request.cookies.get(COOKIE_NAME)) or ""


def require_user(user: str = Depends(current_user)) -> str:
    if not user:
        raise HTTPException(status_code=401, detail="Нужно войти в аккаунт.")
    return user


def _set_cookie(response: Response, username: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_token(username),
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=_SECURE_COOKIE,
    )


# ── конфигурация и авторизация ───────────────────────────────────────────────

@app.get("/api/config")
def get_config(user: str = Depends(current_user)) -> dict[str, Any]:
    return {
        "user": user or None,
        "llm_ready": llm_configured(),
        "stt": get_stt_client() is not None,
        "tts": tts_enabled(),
        "roles": PRESET_ROLES,
        "levels": [{"key": k, "label": LEVEL_LABELS["tech"].get(k, k)} for k in LEVELS],
        "types": [{"key": k, "label": v} for k, v in INTERVIEW_TYPES.items()],
        "modes": [{"key": k, "label": v[0], "hint": v[1]} for k, v in INTERVIEW_MODES.items()],
        "personas": [
            {"key": p.key, "label": p.label, "hint": p.hint} for p in PERSONAS.values()
        ],
        "practice": [{"key": k, "label": v} for k, v in PRACTICE_KINDS.items()],
    }


class Credentials(BaseModel):
    username: str = Field(max_length=80)
    password: str = Field(max_length=200)


@app.post("/api/auth/register")
def auth_register(body: Credentials, response: Response) -> dict[str, Any]:
    error = validate_credentials(body.username, body.password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    if not register(body.username, body.password):
        raise HTTPException(status_code=409, detail="Такой логин уже занят.")
    name = body.username.strip().lower()
    _set_cookie(response, name)
    return {"user": name}


@app.post("/api/auth/login")
def auth_login(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
    name = body.username.strip().lower()
    key = f"{name}|{request.client.host if request.client else ''}"
    if _throttle.blocked(key):
        raise HTTPException(
            status_code=429, detail="Слишком много попыток. Подожди пару минут."
        )
    if not verify(name, body.password):
        _throttle.fail(key)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль.")
    _throttle.reset(key)
    _set_cookie(response, name)
    return {"user": name}


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


# ── интервью: подготовка ─────────────────────────────────────────────────────

class RoleBody(BaseModel):
    role: str = Field(max_length=200)
    custom: bool = False


class VacancyBody(BaseModel):
    text: str = Field(max_length=20000)


class ParamsBody(BaseModel):
    level: str | None = None
    interview_type: str | None = None
    mode: str | None = None
    persona: str | None = None
    stress: bool | None = None
    lang: str | None = None
    skills: str | list[str] | None = None


class AnswerBody(BaseModel):
    text: str = Field(max_length=20000)


class PlanBody(BaseModel):
    days: int = Field(default=7, ge=3, le=14)


class PracticeBody(BaseModel):
    kind: str


class SolutionBody(BaseModel):
    solution: str = Field(max_length=20000)


@app.post("/api/interviews")
def create_interview(user: str = Depends(current_user)) -> dict[str, Any]:
    iv = engine.create(user)
    return engine.setup_view(iv)


@app.get("/api/interviews/{interview_id}")
def get_interview(interview_id: str, user: str = Depends(current_user)) -> dict[str, Any]:
    iv = engine.get(interview_id, user)
    return engine.setup_view(iv)


@app.post("/api/interviews/{interview_id}/role")
def set_role(interview_id: str, body: RoleBody, user: str = Depends(current_user)):
    return engine.set_role(engine.get(interview_id, user), body.role, body.custom)


@app.post("/api/interviews/{interview_id}/vacancy")
def load_vacancy(interview_id: str, body: VacancyBody, user: str = Depends(current_user)):
    return engine.load_vacancy(engine.get(interview_id, user), body.text)


@app.post("/api/interviews/{interview_id}/resume")
async def upload_resume(
    interview_id: str,
    file: UploadFile = File(...),
    user: str = Depends(current_user),
):
    iv = engine.get(interview_id, user)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".pdf", ".docx", ".txt", ".md"):
        raise HTTPException(status_code=400, detail="Поддерживаются PDF, DOCX и TXT.")
    data = await file.read(_MAX_RESUME_BYTES + 1)
    if len(data) > _MAX_RESUME_BYTES:
        raise HTTPException(status_code=400, detail="Файл слишком большой (максимум 5 МБ).")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        # Разбор блокирующий (LLM) — уводим из event loop.
        from starlette.concurrency import run_in_threadpool

        return await run_in_threadpool(engine.load_resume, iv, tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.delete("/api/interviews/{interview_id}/resume")
def delete_resume(interview_id: str, user: str = Depends(current_user)):
    return engine.clear_resume(engine.get(interview_id, user))


@app.post("/api/interviews/{interview_id}/params")
def set_params(interview_id: str, body: ParamsBody, user: str = Depends(current_user)):
    params = body.model_dump(exclude_none=True)
    return engine.set_params(engine.get(interview_id, user), params)


@app.post("/api/interviews/{interview_id}/start")
def start_interview(interview_id: str, user: str = Depends(current_user)):
    return engine.start(engine.get(interview_id, user))


# ── интервью: ход ────────────────────────────────────────────────────────────

@app.get("/api/interviews/{interview_id}/question")
def current_question(interview_id: str, user: str = Depends(current_user)):
    return engine.current_question(engine.get(interview_id, user))


@app.post("/api/interviews/{interview_id}/answer")
def answer(interview_id: str, body: AnswerBody, user: str = Depends(current_user)):
    return engine.answer(engine.get(interview_id, user), body.text)


@app.post("/api/interviews/{interview_id}/skip")
def skip(interview_id: str, user: str = Depends(current_user)):
    return engine.skip(engine.get(interview_id, user))


@app.post("/api/interviews/{interview_id}/finish")
def finish(interview_id: str, user: str = Depends(current_user)):
    return engine.finish(engine.get(interview_id, user))


@app.get("/api/interviews/{interview_id}/report")
def report(interview_id: str, user: str = Depends(current_user)):
    return engine.report_view(engine.get(interview_id, user))


@app.get("/api/interviews/{interview_id}/report.md")
def report_md(interview_id: str, user: str = Depends(current_user)):
    view = engine.report_view(engine.get(interview_id, user))
    return PlainTextResponse(
        report_markdown(view),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="sokrat-report-{interview_id[:8]}.md"'
        },
    )


@app.post("/api/interviews/{interview_id}/plan")
def plan(interview_id: str, body: PlanBody, user: str = Depends(current_user)):
    return engine.study_plan(engine.get(interview_id, user), body.days)


@app.post("/api/interviews/{interview_id}/practice")
def practice(interview_id: str, body: PracticeBody, user: str = Depends(current_user)):
    return engine.practice_generate(engine.get(interview_id, user), body.kind)


@app.post("/api/interviews/{interview_id}/practice/solution")
def practice_solution(
    interview_id: str, body: SolutionBody, user: str = Depends(current_user)
):
    return engine.practice_evaluate(engine.get(interview_id, user), body.solution)


# ── голос ────────────────────────────────────────────────────────────────────

@app.post("/api/stt")
async def speech_to_text(request: Request, user: str = Depends(current_user)):
    client = get_stt_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Распознавание речи не настроено.")
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="Пустая запись.")
    if len(audio) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Запись слишком длинная (до ~1 минуты).")
    from starlette.concurrency import run_in_threadpool

    try:
        text = await run_in_threadpool(client.recognize_pcm16, audio, 16000)
    except SaluteSpeechError as exc:
        log.warning("STT failed: %s", exc)
        raise HTTPException(status_code=502, detail="Не удалось распознать речь.") from exc
    return {"text": text}


class TtsBody(BaseModel):
    text: str = Field(max_length=1500)


@app.post("/api/tts")
async def text_to_speech(body: TtsBody, user: str = Depends(current_user)):
    client = get_stt_client()
    if client is None or not tts_enabled():
        raise HTTPException(status_code=503, detail="Озвучка не настроена.")
    from starlette.concurrency import run_in_threadpool

    clean = re.sub(r"[`*_>#]+", "", body.text).strip()
    try:
        audio = await run_in_threadpool(client.synthesize_wav, clean)
    except SaluteSpeechError as exc:
        log.warning("TTS failed: %s", exc)
        raise HTTPException(status_code=502, detail="Не удалось озвучить текст.") from exc
    return Response(content=audio, media_type="audio/wav")


# ── личный кабинет ───────────────────────────────────────────────────────────

def _legacy_to_state(data: dict[str, Any]) -> dict[str, Any]:
    """Старые сессии (summary + qa_pairs) приводим к форме, понятной build_report_view."""
    pairs = data.get("qa_pairs") or []
    return {
        **data,
        "questions": [{"question": p.get("question_text", "")} for p in pairs],
        "answers": [p.get("answer", "") for p in pairs],
        "scores": [
            {k: p.get(k) for k in (
                "score", "verdict", "feedback", "strengths", "weaknesses",
                "category", "ideal_answer", "elapsed_seconds",
            )}
            for p in pairs
        ],
    }


@app.get("/api/cabinet")
def cabinet(user: str = Depends(require_user)) -> dict[str, Any]:
    records = [r for r in load_sessions() if r["user_id"] == user]
    records.sort(key=lambda r: r["ts"])

    by_role: dict[str, list[dict[str, Any]]] = {}
    companies: dict[str, dict[str, Any]] = {}
    for r in records:
        by_role.setdefault(r["role"] or "—", []).append(
            {"ts": r["ts"], "percentage": r["percentage"], "session_id": r["session_id"]}
        )
        if r["company"] and r["readiness"] is not None:
            companies[r["company"].lower()] = {
                "company": r["company"],
                "role": r["role"],
                "readiness": r["readiness"],
                "ts": r["ts"],
            }

    pcts = [r["percentage"] for r in records]
    weak: list[dict[str, str]] = []
    for role in list(by_role)[:5]:
        for topic in recent_weak_topics(role, user, limit=3):
            weak.append({"role": role, "topic": topic})

    return {
        "user": user,
        "stats": {
            "sessions": len(records),
            "average": round(sum(pcts) / len(pcts), 1) if pcts else 0.0,
            "best": max(pcts) if pcts else 0.0,
            "trend": round(pcts[-1] - pcts[0], 1) if len(pcts) > 1 else None,
        },
        "sessions": [
            {
                "session_id": r["session_id"],
                "ts": r["ts"],
                "role": r["role"],
                "level": r["level"],
                "company": r["company"],
                "percentage": r["percentage"],
                "readiness": r["readiness"],
            }
            for r in reversed(records)
        ][:50],
        "progress": by_role,
        "companies": sorted(companies.values(), key=lambda c: c["readiness"], reverse=True)[:6],
        "weak_topics": weak[:8],
    }


@app.get("/api/sessions/{session_id}")
def saved_session(session_id: str, user: str = Depends(require_user)) -> dict[str, Any]:
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    path = SESSIONS_DIR / f"{session_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="Сессия не найдена.") from None
    if str(data.get("user_id") or "") != user:
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    if "qa_pairs" in data and "scores" not in data:
        data = _legacy_to_state(data)
    view = build_report_view(data)
    view["can_plan"] = False
    view["saved"] = True
    return view


# ── статика ──────────────────────────────────────────────────────────────────

app.mount("/public", StaticFiles(directory=str(PUBLIC_DIR)), name="public")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
