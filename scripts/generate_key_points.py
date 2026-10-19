#!/usr/bin/env python3
"""
Офлайн-генерация эталонных пунктов (key_points) для вопросов банка data/questions.json.

Запуск из корня репозитория:
  python -m scripts.generate_key_points --limit 10 --dry-run
  python -m scripts.generate_key_points --ids py_junior_tech_4,pm_junior_hr_1 --dry-run
  python -m scripts.generate_key_points            # весь банк

Идемпотентен: вопросы с валидными key_points и с key_points_source="human" не трогает.
Пишет key_points_source="llm" — это черновик, не эталон, проверенный человеком.
Окружение — как у eval/eval_judge.py: LLM_API_KEY или DEEPSEEK_API_KEY, LLM_BASE_URL.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import re
import sys
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sokrat.interview_rules import _atomic_write_json
from sokrat.key_points import validate_bonus_points, validate_key_points
from sokrat.prompts import load_prompt

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BANK = REPO_ROOT / "data" / "questions.json"
DEFAULT_MODEL = "deepseek-v4-pro"
TEMPERATURE = 0.0
ATTEMPTS = 2  # первый вызов + один повтор при невалидном ответе
SOURCE_LLM = "llm"
SOURCE_HUMAN = "human"

MOTIVATIONAL_TYPES = frozenset({"hr", "mixed"})
MOTIVATIONAL_RE = re.compile(
    r"почему\s+(ты\s+)?(хочешь|решил|идёшь|ищешь|уходишь|ушёл)"
    r"|почему\s+(ты\s+)?выбрал\w*\s+(\w+\s+){0,3}(как\s+основн|язык|професси|роль|направлени|сфер|компани|сторон)"
    r"|почему\s+(именно\s+)?(мы|нас|наш\w*|эт\w+\s+(компани|роль|должност|позици|професси|сфер))\b"
    r"|(какие|каковы)\s+(у\s+тебя\s+)?(твои\s+)?(карьерн\w*\s+)?(цели|планы)"
    r"|где\s+(ты\s+)?видишь\s+себя"
    r"|что\s+тебя\s+(мотивирует|привлекает|вдохновляет)|чем\s+тебя\s+привлек|тво\w+\s+мотивац"
    r"|why\s+do\s+you\s+want|why\s+(us|our\s+company)|career\s+goals|what\s+motivates\s+you",
    re.IGNORECASE,
)
KIND_MOTIVATIONAL = "мотивационный — используй схему для мотивационных вопросов, без STAR."
KIND_OTHER = ("технический или поведенческий — определи сам по смыслу вопроса "
              "(вопрос о собственном опыте кандидата — поведенческий).")

log = logging.getLogger("generate_key_points")

LlmCall = Callable[[str], str]


@dataclass(frozen=True)
class QuestionRef:
    role: str
    level: str
    interview_type: str
    index: int
    question: dict[str, Any]


@dataclass(frozen=True)
class GeneratedPoints:
    key_points: list[str]
    bonus_points: list[str]


@dataclass
class RunSummary:
    generated: list[tuple[QuestionRef, GeneratedPoints]] = field(default_factory=list)
    skipped_invalid: list[str] = field(default_factory=list)
    already_filled: int = 0
    llm_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def count_call(self) -> None:
        with self._lock:
            self.llm_calls += 1


def iter_questions(db: dict[str, Any]) -> Iterator[QuestionRef]:
    for role, levels in (db.get("roles") or {}).items():
        for level, types in levels.items():
            for interview_type, questions in types.items():
                for index, question in enumerate(questions):
                    if isinstance(question, dict):
                        yield QuestionRef(role, level, interview_type, index, question)


def needs_key_points(question: dict[str, Any]) -> bool:
    if question.get("key_points_source") == SOURCE_HUMAN:
        return False
    return validate_key_points(question.get("key_points")) is None


def is_motivational(question: str, interview_type: str) -> bool:
    """Мотивационный вопрос: hr/mixed + формулировка «почему ты хочешь…», «почему мы», цели, мотивация.

    «Почему ты выбрал такой подход» — не мотивационный (это про решение), поэтому «выбрал»
    считается только с объектом-карьерой: язык, профессия, роль, направление, компания.
    """
    return interview_type in MOTIVATIONAL_TYPES and bool(MOTIVATIONAL_RE.search(question))


def question_kind(ref: QuestionRef) -> str:
    if is_motivational(ref.question.get("question", ""), ref.interview_type):
        return KIND_MOTIVATIONAL
    return KIND_OTHER


def build_prompt(template: str, ref: QuestionRef) -> str:
    q = ref.question
    hints = q.get("hints") or []
    keywords = q.get("ideal_keywords") or []
    return template.format(
        role=ref.role,
        level=ref.level,
        interview_type=ref.interview_type,
        question=q.get("question", ""),
        hints=", ".join(hints) if hints else "(нет)",
        ideal_keywords=", ".join(keywords) if keywords else "(нет)",
        question_kind=question_kind(ref),
    )


def parse_key_points(raw: str) -> GeneratedPoints | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    key_points = validate_key_points(data.get("key_points"))
    if key_points is None:
        return None
    bonus_points = validate_bonus_points(data.get("bonus_points"), key_points)
    if bonus_points is None:
        return None
    return GeneratedPoints(key_points, bonus_points)


def generate_for(call_llm: LlmCall, prompt: str, summary: RunSummary) -> tuple[GeneratedPoints | None, str]:
    """(пункты или None, причина неудачи последней попытки)."""
    reason = ""
    for _ in range(ATTEMPTS):
        summary.count_call()
        try:
            raw = call_llm(prompt)
        except Exception as exc:  # noqa: BLE001 — один вопрос не должен ронять весь прогон
            reason = f"{type(exc).__name__}: {exc}"
            continue
        points = parse_key_points(raw)
        if points is not None:
            return points, ""
        reason = f"невалидный ответ: {raw[:200]!r}"
    return None, reason


def with_key_points(db: dict[str, Any], ref: QuestionRef, points: GeneratedPoints) -> dict[str, Any]:
    """Новая копия банка с пунктами у одного вопроса; остальные поля вопроса не трогаются."""
    updated = copy.deepcopy(db)
    target = updated["roles"][ref.role][ref.level][ref.interview_type][ref.index]
    target["key_points"] = list(points.key_points)
    target["bonus_points"] = list(points.bonus_points)
    target["key_points_source"] = SOURCE_LLM
    return updated


def select_refs(db: dict[str, Any], ids: set[str] | None, limit: int | None) -> tuple[list[QuestionRef], int]:
    """(вопросы к генерации, сколько пропущено как уже заполненные)."""
    todo, filled = [], 0
    for ref in iter_questions(db):
        if ids is not None and ref.question.get("id") not in ids:
            continue
        if not needs_key_points(ref.question):
            filled += 1
            continue
        todo.append(ref)
    return (todo[:limit] if limit is not None else todo), filled


def run(
    bank_path: Path,
    call_llm: LlmCall,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    ids: set[str] | None = None,
    workers: int = 1,
    write: Callable[[Path, dict], None] = _atomic_write_json,
) -> RunSummary:
    db = json.loads(Path(bank_path).read_text(encoding="utf-8"))
    template = load_prompt("key_points")
    refs, filled = select_refs(db, ids, limit)
    summary = RunSummary(already_filled=filled)
    with ThreadPoolExecutor(max(1, workers)) as pool:
        # map сохраняет порядок; вызовы параллельны, запись в банк — только в этом потоке
        results = pool.map(lambda r: (r, *generate_for(call_llm, build_prompt(template, r), summary)), refs)
        for ref, points, reason in results:
            qid = ref.question.get("id", "?")
            if points is None:
                log.warning("%s: пропущен после %d попыток — %s", qid, ATTEMPTS, reason)
                summary.skipped_invalid.append(qid)
                continue
            summary.generated.append((ref, points))
            if not dry_run:
                db = with_key_points(db, ref, points)
                write(Path(bank_path), db)  # после каждого вопроса: прерванный прогон не теряет работу
    return summary


@dataclass
class UsageMeter:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls_with_usage: int = 0

    def add(self, usage: Any) -> None:
        if usage is None:
            return
        self.prompt_tokens += usage.prompt_tokens or 0
        self.completion_tokens += usage.completion_tokens or 0
        self.calls_with_usage += 1


def make_llm_call(model: str, meter: UsageMeter) -> LlmCall:
    from eval.eval_judge import make_client

    client = make_client()

    def call(prompt: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            temperature=TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        meter.add(resp.usage)
        return resp.choices[0].message.content or ""

    return call


def print_summary(summary: RunSummary, dry_run: bool) -> None:
    for ref, points in summary.generated:
        q = ref.question
        print(f"\n## {q.get('id')} — {ref.role} / {ref.level} / {ref.interview_type}")
        print(f"Вопрос: {q.get('question')}")
        print(f"hints: {', '.join(q.get('hints') or [])}")
        print(f"ideal_keywords: {', '.join(q.get('ideal_keywords') or [])}")
        for n, p in enumerate(points.key_points, 1):
            print(f"  {n}. {p}")
        for p in points.bonus_points:
            print(f"  + {p}")
    mode = "dry-run, файл не изменён" if dry_run else "записано"
    print(f"\nСгенерировано: {len(summary.generated)} ({mode}) | пропущено с ошибкой: "
          f"{len(summary.skipped_invalid)} {summary.skipped_invalid or ''} | уже заполнены: "
          f"{summary.already_filled} | вызовов LLM: {summary.llm_calls}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bank", default=str(DEFAULT_BANK))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, help="не больше N вопросов к генерации")
    parser.add_argument("--ids", help="только эти id через запятую")
    parser.add_argument("--dry-run", action="store_true", help="показать пункты, файл не менять")
    parser.add_argument("--workers", type=int, default=1, help="параллельных вызовов LLM")
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit < 0:
        parser.error("--limit должен быть ≥ 0")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ids = {i.strip() for i in args.ids.split(",") if i.strip()} if args.ids else None
    meter = UsageMeter()
    summary = run(Path(args.bank), make_llm_call(args.model, meter), limit=args.limit,
                  dry_run=args.dry_run, ids=ids, workers=args.workers)
    print_summary(summary, args.dry_run)
    print(f"Токены: вход {meter.prompt_tokens}, выход {meter.completion_tokens} "
          f"(usage есть у {meter.calls_with_usage}/{summary.llm_calls} вызовов)")
    return 0 if not summary.skipped_invalid else 1


if __name__ == "__main__":
    sys.exit(main())
