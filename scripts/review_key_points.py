#!/usr/bin/env python3
"""
Ревью эталонных пунктов банка другой моделью: только помечает, ничего не исправляет.

Запуск из корня репозитория:
  python -m scripts.review_key_points --prompt ../SokaratAI/.local/eval/reviewer_prompt.txt \
      --report ../SokaratAI/.local/reports/2026-10-19_key-points-review.md

Без ключа OpenRouter — ручные пачки для чата с Claude:
  python -m scripts.review_key_points --prompt ../SokaratAI/.local/eval/reviewer_prompt.txt \
      --export-batches ../SokaratAI/.local/eval/review_batches
  # ответ на batch_NN.md сохранить как batch_NN.response.json в той же папке
  python -m scripts.review_key_points --import-batches ../SokaratAI/.local/eval/review_batches

Рецензент — не та модель, что генерировала пункты: по умолчанию anthropic/claude-sonnet-4-5
через OpenRouter (OPENROUTER_API_KEY, OPENROUTER_BASE_URL из .env), t=0.
Промпт рецензента лежит вне git; формат ответа скрипт задаёт сам (JSON вместо таблицы).
Пишет только поле key_points_flags (список; [] — замечаний нет). Повторный запуск пропускает
вопросы с уже заполненным key_points_flags (--force — перепроверить). Вопросы с
key_points_source="human" пропускаются (--include-human — проверить и их).
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from scripts.generate_key_points import DEFAULT_BANK, REPO_ROOT, QuestionRef, UsageMeter, iter_questions
from sokrat.interview_rules import _atomic_write_json
from sokrat.key_points import validate_key_points

DEFAULT_MODEL = "anthropic/claude-sonnet-4-5"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
TEMPERATURE = 0.0
ATTEMPTS = 2
SEVERITIES = ("высокая", "средняя", "низкая")
CRITERIA = frozenset(range(1, 8))
MISSING = "missing"  # пункт флага для «чего не хватает в списке»

log = logging.getLogger("review_key_points")

LlmCall = Callable[[str], str]

_TASK = """

══════════════════════════════════════
Вопрос для проверки

id: {id}
роль: {role} | уровень: {level} | тип интервью: {interview_type}
Вопрос: {question}

Обязательные пункты (балл считается из доли раскрытых):
{key_points}

Бонусные пункты (в долю не входят; раскрытый бонус даёт +1 к баллу):
{bonus_points}

══════════════════════════════════════
Формат ответа — ВМЕСТО таблицы из инструкции выше: СТРОГО один JSON-объект без markdown и пояснений.
{{"flags": [{{"point": "<номер обязательного пункта, B1.. для бонусного или \\"missing\\" для пропущенного важного пункта>",
             "criterion": <1-7; для missing — 3>,
             "problem": "<проблема>",
             "suggestion": "<предложенная формулировка>",
             "severity": "<высокая|средняя|низкая>"}}]}}
Если проблем нет — {{"flags": []}}. Общие правила для генератора здесь не нужны.
"""


_BATCH_QUESTION = """
──────────────────────────────────────
id: {id}
роль: {role} | уровень: {level} | тип интервью: {interview_type}
Вопрос: {question}

Обязательные пункты (балл считается из доли раскрытых):
{key_points}

Бонусные пункты (в долю не входят; раскрытый бонус даёт +1 к баллу):
{bonus_points}
"""

_BATCH_FORMAT = """
══════════════════════════════════════
Формат ответа — ВМЕСТО таблицы из инструкции выше: верни ТОЛЬКО один JSON-объект, без markdown,
без пояснений до или после. В reviews — ровно по одному элементу на КАЖДЫЙ id выше ({count} шт.),
в том же порядке; если у вопроса проблем нет — "flags": [].
{{"reviews": [{{"id": "<id вопроса>",
               "flags": [{{"point": "<номер обязательного пункта, B1.. для бонусного или \\"missing\\" для пропущенного важного пункта>",
                          "criterion": <1-7; для missing — 3>,
                          "problem": "<проблема>",
                          "suggestion": "<предложенная формулировка>",
                          "severity": "<высокая|средняя|низкая>"}}]}}]}}
Общие правила для генератора здесь не нужны.
"""

BATCH_SIZE = 20


@dataclass
class ReviewSummary:
    reviewed: list[tuple[QuestionRef, list[dict[str, Any]]]] = field(default_factory=list)
    skipped_invalid: list[str] = field(default_factory=list)
    skipped_done: int = 0
    skipped_human: int = 0
    skipped_no_points: int = 0
    llm_calls: int = 0


def _numbered(points: list[str], prefix: str = "") -> str:
    if not points:
        return "(нет)"
    return "\n".join(f"{prefix}{n}. {p}" for n, p in enumerate(points, 1))


def build_prompt(reviewer_prompt: str, ref: QuestionRef) -> str:
    q = ref.question
    return reviewer_prompt + _TASK.format(
        id=q.get("id", ""),
        role=ref.role,
        level=ref.level,
        interview_type=ref.interview_type,
        question=q.get("question", ""),
        key_points=_numbered(q.get("key_points") or []),
        bonus_points=_numbered(q.get("bonus_points") or [], prefix="B"),
    )


def _extract_json(raw: str) -> Any:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _valid_point(point: Any, n_key: int, n_bonus: int) -> str | None:
    value = str(point).strip()
    if value == MISSING:
        return value
    if value.isdigit() and 1 <= int(value) <= n_key:
        return value
    if re.fullmatch(r"[BbВв]\d+", value) and 1 <= int(value[1:]) <= n_bonus:
        return "B" + value[1:]
    return None


def parse_flags(raw: str, n_key: int, n_bonus: int) -> list[dict[str, Any]] | None:
    """Список флагов или None, если ответ не по формату (тогда повтор)."""
    data = _extract_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("flags"), list):
        return None
    flags = []
    for item in data["flags"]:
        if not isinstance(item, dict):
            return None
        point = _valid_point(item.get("point"), n_key, n_bonus)
        criterion = item.get("criterion")
        severity = str(item.get("severity", "")).strip().lower()
        if point is None or severity not in SEVERITIES:
            return None
        if isinstance(criterion, str) and criterion.strip().isdigit():
            criterion = int(criterion.strip())
        if criterion not in CRITERIA:
            return None
        flags.append({
            "point": point,
            "criterion": criterion,
            "severity": severity,
            "problem": str(item.get("problem", "")).strip(),
            "suggestion": str(item.get("suggestion", "")).strip(),
        })
    return flags


def select_refs(db, ids, limit, force, include_human, summary):
    todo = []
    for ref in iter_questions(db):
        q = ref.question
        if ids is not None and q.get("id") not in ids:
            continue
        if validate_key_points(q.get("key_points")) is None:
            summary.skipped_no_points += 1
            continue
        if q.get("key_points_source") == "human" and not include_human:
            summary.skipped_human += 1
            continue
        if "key_points_flags" in q and not force:
            summary.skipped_done += 1
            continue
        todo.append(ref)
    return todo[:limit] if limit is not None else todo


def review_one(call_llm: LlmCall, reviewer_prompt: str, ref: QuestionRef) -> tuple[list | None, int, str]:
    """(флаги или None, число вызовов, причина неудачи)."""
    q = ref.question
    prompt = build_prompt(reviewer_prompt, ref)
    reason, calls = "", 0
    for _ in range(ATTEMPTS):
        calls += 1
        try:
            raw = call_llm(prompt)
        except Exception as exc:  # noqa: BLE001 — один вопрос не должен ронять весь прогон
            reason = f"{type(exc).__name__}: {exc}"
            continue
        flags = parse_flags(raw, len(q.get("key_points") or []), len(q.get("bonus_points") or []))
        if flags is not None:
            return flags, calls, ""
        reason = f"невалидный ответ: {raw[:200]!r}"
    return None, calls, reason


def with_flags(db: dict[str, Any], ref: QuestionRef, flags: list[dict[str, Any]]) -> dict[str, Any]:
    """Новая копия банка; у вопроса меняется только key_points_flags."""
    updated = copy.deepcopy(db)
    updated["roles"][ref.role][ref.level][ref.interview_type][ref.index]["key_points_flags"] = flags
    return updated


def run(
    bank_path: Path,
    call_llm: LlmCall,
    reviewer_prompt: str,
    *,
    limit: int | None = None,
    ids: set[str] | None = None,
    force: bool = False,
    include_human: bool = False,
    dry_run: bool = False,
    workers: int = 1,
    write: Callable[[Path, dict], None] = _atomic_write_json,
) -> ReviewSummary:
    db = json.loads(Path(bank_path).read_text(encoding="utf-8"))
    summary = ReviewSummary()
    refs = select_refs(db, ids, limit, force, include_human, summary)
    with ThreadPoolExecutor(max(1, workers)) as pool:
        results = pool.map(lambda r: (r, *review_one(call_llm, reviewer_prompt, r)), refs)
        for ref, flags, calls, reason in results:  # map сохраняет порядок; запись — в одном потоке
            summary.llm_calls += calls
            qid = ref.question.get("id", "?")
            if flags is None:
                log.warning("%s: пропущен после %d попыток — %s", qid, ATTEMPTS, reason)
                summary.skipped_invalid.append(qid)
                continue
            summary.reviewed.append((ref, flags))
            if not dry_run:
                db = with_flags(db, ref, flags)
                write(Path(bank_path), db)
    return summary


# ---------------------------------------------------------------- ручные пачки (без API)

def build_batch_prompt(reviewer_prompt: str, refs: list[QuestionRef]) -> str:
    """Самодостаточный текст для чата: промпт рецензента + вопросы + требование вернуть JSON."""
    blocks = [
        _BATCH_QUESTION.format(
            id=r.question.get("id", ""), role=r.role, level=r.level, interview_type=r.interview_type,
            question=r.question.get("question", ""),
            key_points=_numbered(r.question.get("key_points") or []),
            bonus_points=_numbered(r.question.get("bonus_points") or [], prefix="B"),
        )
        for r in refs
    ]
    header = f"\n\n══════════════════════════════════════\nВопросы для проверки ({len(refs)} шт.)\n"
    return reviewer_prompt + header + "".join(blocks) + _BATCH_FORMAT.format(count=len(refs))


def export_batches(
    bank_path: Path, reviewer_prompt: str, out_dir: Path, *, batch_size: int = BATCH_SIZE,
    ids: set[str] | None = None, force: bool = False, include_human: bool = False,
) -> tuple[list[Path], ReviewSummary]:
    """Пишет batch_NN.md (вставить в чат целиком) и batch_NN.ids.json (какие id в пачке)."""
    db = json.loads(Path(bank_path).read_text(encoding="utf-8"))
    summary = ReviewSummary()
    refs = select_refs(db, ids, None, force, include_human, summary)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for n, start in enumerate(range(0, len(refs), batch_size), 1):
        chunk = refs[start:start + batch_size]
        md = out_dir / f"batch_{n:02d}.md"
        md.write_text(build_batch_prompt(reviewer_prompt, chunk), encoding="utf-8")
        (out_dir / f"batch_{n:02d}.ids.json").write_text(
            json.dumps([r.question.get("id") for r in chunk], ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(md)
    return paths, summary


def parse_batch_response(raw: str, refs_by_id: dict[str, QuestionRef]) -> tuple[dict[str, list], list[str]]:
    """({id: флаги} для валидных вопросов, ошибки). Невалидный вопрос не валит всю пачку."""
    data = _extract_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        return {}, ["ответ не JSON-объект с полем reviews"]
    found, errors = {}, []
    for item in data["reviews"]:
        qid = item.get("id") if isinstance(item, dict) else None
        ref = refs_by_id.get(qid)
        if ref is None:
            errors.append(f"{qid!r}: нет в этой пачке")
            continue
        q = ref.question
        flags = parse_flags(json.dumps({"flags": item.get("flags")}, ensure_ascii=False),
                            len(q.get("key_points") or []), len(q.get("bonus_points") or []))
        if flags is None:
            errors.append(f"{qid}: флаги не по формату")
            continue
        found[qid] = flags
    errors += [f"{qid}: нет в ответе" for qid in refs_by_id if qid not in found and
               not any(e.startswith(f"{qid}:") for e in errors)]
    return found, errors


def import_batches(
    bank_path: Path, batch_dir: Path, *, write: Callable[[Path, dict], None] = _atomic_write_json,
    dry_run: bool = False,
) -> tuple[ReviewSummary, list[str]]:
    """Читает batch_NN.response.json рядом с batch_NN.ids.json и пишет key_points_flags."""
    db = json.loads(Path(bank_path).read_text(encoding="utf-8"))
    refs_all = {r.question.get("id"): r for r in iter_questions(db)}
    summary, errors = ReviewSummary(), []
    for ids_path in sorted(batch_dir.glob("batch_*.ids.json")):
        resp_path = ids_path.with_name(ids_path.name.replace(".ids.json", ".response.json"))
        if not resp_path.exists():
            errors.append(f"{resp_path.name}: ответа нет — пачка пропущена")
            continue
        batch_ids = json.loads(ids_path.read_text(encoding="utf-8"))
        refs = {qid: refs_all[qid] for qid in batch_ids if qid in refs_all}
        found, batch_errors = parse_batch_response(resp_path.read_text(encoding="utf-8"), refs)
        errors += [f"{resp_path.name}: {e}" for e in batch_errors]
        summary.skipped_invalid += [qid for qid in refs if qid not in found]
        for qid, flags in found.items():
            summary.reviewed.append((refs[qid], flags))
            db = with_flags(db, refs[qid], flags)
    if summary.reviewed and not dry_run:
        write(Path(bank_path), db)
    return summary, errors


def make_llm_call(model: str, meter: UsageMeter) -> LlmCall:
    from openai import OpenAI

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        sys.exit("Нет OPENROUTER_API_KEY (в окружении или .env)")
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENROUTER_BASE_URL", "").strip() or DEFAULT_BASE_URL,
        timeout=180,
        max_retries=2,
        default_headers={"HTTP-Referer": os.getenv("OPENROUTER_REFERER") or "https://sokrat.local",
                         "X-Title": "Sokrat key_points review"},
    )

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


def _point_text(q: dict[str, Any], point: str) -> str:
    if point == MISSING:
        return "(пропущен важный пункт)"
    if point.startswith("B"):
        bonus = q.get("bonus_points") or []
        return f"B{point[1:]}. {bonus[int(point[1:]) - 1]}"
    return f"{point}. {(q.get('key_points') or [])[int(point) - 1]}"


def render_report(summary: ReviewSummary, model: str, meter: UsageMeter) -> str:
    flags = [(ref, f) for ref, fs in summary.reviewed for f in fs]
    by_sev = {s: sum(1 for _, f in flags if f["severity"] == s) for s in SEVERITIES}
    flagged_q = sum(1 for _, fs in summary.reviewed if fs)
    lines = [
        f"# Ревью key_points — {model}", "",
        f"Проверено вопросов: {len(summary.reviewed)} | с замечаниями: {flagged_q} | "
        f"флагов: {len(flags)} (высокая {by_sev['высокая']}, средняя {by_sev['средняя']}, "
        f"низкая {by_sev['низкая']})",
        f"Пропущено: невалидный ответ {len(summary.skipped_invalid)} {summary.skipped_invalid or ''}; "
        f"уже проверены {summary.skipped_done}; human {summary.skipped_human}; "
        f"без key_points {summary.skipped_no_points}",
        f"Вызовов: {summary.llm_calls} | токены: вход {meter.prompt_tokens}, выход {meter.completion_tokens}",
        "",
    ]
    for sev in SEVERITIES:
        rows = [(ref, f) for ref, f in flags if f["severity"] == sev]
        if not rows:
            continue
        lines += [f"## Серьёзность: {sev} ({len(rows)})", "",
                  "| id | уровень | пункт | крит. | проблема | предложение |", "|---|---|---|---|---|---|"]
        for ref, f in rows:
            cells = [ref.question.get("id", ""), ref.level, _point_text(ref.question, f["point"]),
                     str(f["criterion"]), f["problem"], f["suggestion"]]
            lines.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
        lines.append("")
    return "\n".join(lines)


def _cli_import(args: argparse.Namespace) -> int:
    summary, errors = import_batches(Path(args.bank), Path(args.import_batches), dry_run=args.dry_run)
    for e in errors:
        log.warning(e)
    report = render_report(summary, "ручные пачки (чат)", UsageMeter())
    print(report)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report, encoding="utf-8")
    return 0 if not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompt", help="промпт рецензента (вне git); нужен для ревью и --export-batches")
    parser.add_argument("--export-batches", metavar="DIR",
                        help="без API: записать пачки batch_NN.md для ручного прогона в чате")
    parser.add_argument("--import-batches", metavar="DIR",
                        help="без API: прочитать batch_NN.response.json и записать key_points_flags")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--bank", default=str(DEFAULT_BANK))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--report", help="куда записать markdown-отчёт")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", help="только эти id через запятую")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="перепроверить уже проверенные")
    parser.add_argument("--include-human", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="не писать в банк")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ids = {i.strip() for i in args.ids.split(",") if i.strip()} if args.ids else None
    if args.import_batches:
        return _cli_import(args)
    if not args.prompt:
        parser.error("нужен --prompt")
    reviewer_prompt = Path(args.prompt).read_text(encoding="utf-8")
    if args.export_batches:
        paths, summary = export_batches(Path(args.bank), reviewer_prompt, Path(args.export_batches),
                                        batch_size=args.batch_size, ids=ids, force=args.force,
                                        include_human=args.include_human)
        n_questions = sum(len(json.loads(p.with_name(p.stem + ".ids.json").read_text(encoding="utf-8")))
                          for p in paths)
        print(f"Пачек: {len(paths)} | вопросов: {n_questions} | пропущено: human {summary.skipped_human}, "
              f"уже проверены {summary.skipped_done}, без key_points {summary.skipped_no_points}")
        print(f"Ответ каждой пачки сохрани рядом как batch_NN.response.json, затем --import-batches "
              f"{args.export_batches}")
        return 0
    meter = UsageMeter()
    summary = run(Path(args.bank), make_llm_call(args.model, meter), reviewer_prompt,
                  limit=args.limit, ids=ids, force=args.force, include_human=args.include_human,
                  dry_run=args.dry_run, workers=args.workers)
    report = render_report(summary, args.model, meter)
    print(report)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report, encoding="utf-8")
    return 0 if not summary.skipped_invalid else 1


if __name__ == "__main__":
    sys.exit(main())
