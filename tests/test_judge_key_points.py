"""Судья, scoring, engine и eval с фиксированными и бонусными пунктами (без сети)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from eval.eval_judge import build_prompt, call_judge, final_score, parse_stability
from sokrat import analyzer as analyzer_mod
from sokrat.analyzer import AnswerEvaluation, analyze_answer_llm
from sokrat.engine import Engine, judge_points
from sokrat.key_points import format_fixed_points
from sokrat.scoring import compute_score, has_bonus

POINTS = ["GIL пускает исполнять байткод одному потоку", "CPU-bound потоками не ускорить",
          "для IO-bound потоки помогают"]


# ── судья: analyze_answer_llm с фиксированными пунктами ──────────────────────

def _evaluation(points, **extra) -> dict:
    return {"intent": "answer", "feedback": "ok", "score": 6, "points": points, "errors": [],
            "expected_points": [], "bonus": False, "off_topic": False, **extra}


class FakeStructured:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list = []

    def invoke(self, messages):
        self.calls.append(messages)
        return self.outputs.pop(0)


@pytest.fixture
def fake_judge(monkeypatch):
    def install(*outputs):
        structured = FakeStructured(outputs)
        model = SimpleNamespace(with_structured_output=lambda *a, **k: structured)
        monkeypatch.setattr(analyzer_mod, "get_chat_model_for", lambda *a, **k: model)
        return structured
    return install


def _judge(**kwargs) -> AnswerEvaluation:
    return analyze_answer_llm(role="Python Developer", level="Junior", interview_type="technical",
                              question="Что такое GIL?", hints=[], answer="ответ", clarifications_used=0,
                              **kwargs)


def test_judge_prompt_lists_fixed_points_and_markup_is_restored(fake_judge):
    marked = [{"i": n, "status": "covered", "evidence": "e"} for n in (1, 2, 3)]
    structured = fake_judge(_evaluation(marked))

    result = _judge(fixed_points=POINTS, key_points=["GIL"])

    prompt = structured.calls[0][0].content
    assert f"1. {POINTS[0]}" in prompt and "Ключевые слова темы (служебно, только как подсказка): GIL" in prompt
    assert result.expected_points == POINTS
    assert [p["point"] for p in result.points] == POINTS
    assert result.points_mismatch is False


def test_judge_retries_unmatched_markup_then_flags_mismatch(fake_judge):
    bad = [{"i": 1, "status": "covered", "evidence": ""}]
    structured = fake_judge(_evaluation(bad), _evaluation(bad))

    result = _judge(fixed_points=POINTS)

    assert len(structured.calls) == 2
    assert result.points_mismatch is True


def test_judge_without_fixed_points_keeps_old_behaviour(fake_judge):
    points = [{"point": "a", "status": "covered", "evidence": ""}]
    structured = fake_judge(_evaluation(points, expected_points=["a"]))

    result = _judge(key_points=["GIL"])

    assert "Эталонные пункты ответа (служебно, фиксированы):\n(не заданы)" in structured.calls[0][0].content
    assert result.points == points and result.points_mismatch is False


def test_judge_non_answer_intent_is_not_matched_against_points(fake_judge):
    fake_judge({"intent": "clarification", "feedback": "переформулирую", "score": None})

    result = _judge(fixed_points=POINTS)

    assert result.intent == "clarification" and result.points_mismatch is False


# ── engine ──────────────────────────────────────────────────────────────────

def test_judge_points_prefers_key_points_and_keeps_keywords_as_hints():
    question = {"key_points": POINTS, "bonus_points": ["b"], "key_points_source": "llm",
                "ideal_keywords": ["GIL", "thread"]}
    assert judge_points(question) == {"fixed_points": POINTS, "bonus_points": ["b"],
                                      "key_points": ["GIL", "thread"]}


def test_judge_points_falls_back_to_ideal_keywords():
    assert judge_points({"ideal_keywords": ["GIL"]}) == {"fixed_points": None, "bonus_points": None,
                                                         "key_points": ["GIL"]}
    assert judge_points({}) == {"fixed_points": None, "bonus_points": None, "key_points": []}


def test_answer_score_uses_model_score_on_points_mismatch():
    analysis = AnswerEvaluation(intent="answer", feedback="", score=7, points_mismatch=True,
                                points=[{"status": "missing"}], errors=[])
    assert Engine._answer_score(analysis) == 7


def test_answer_score_computes_from_points_when_matched():
    analysis = AnswerEvaluation(intent="answer", feedback="", score=7, bonus=False, off_topic=False,
                                points=[{"status": "covered"}] * 3, errors=[])
    assert Engine._answer_score(analysis) == 9


# ── eval ────────────────────────────────────────────────────────────────────

ITEM = {"id": "py_gil_base", "question": "Что такое GIL?", "answer": "ответ", "key_points": POINTS}
NEW_TEMPLATE = "{key_points}|{fixed_points}|{answer}"
OLD_TEMPLATE = "{key_points}|{answer}"


def test_eval_build_prompt_routes_key_points_by_template():
    assert build_prompt(NEW_TEMPLATE, ITEM, "p", False) == f"(не заданы)|{format_fixed_points(POINTS)}|ответ"
    assert build_prompt(OLD_TEMPLATE, ITEM, "p", False) == f"{'; '.join(POINTS)}|ответ"


class FakeClient:
    def __init__(self, *raws: str):
        self.raws = list(raws)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        raw = self.raws.pop(0)
        return SimpleNamespace(usage=None, model="m", choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])


def _raw(points, score=6):
    return json.dumps(_evaluation(points, score=score), ensure_ascii=False)


def test_eval_call_judge_restores_fixed_points_and_scores_by_code():
    marked = [{"i": n, "status": "covered", "evidence": ""} for n in (1, 2, 3)]

    rec = call_judge(FakeClient(_raw(marked)), "m", 0, "prompt", fixed=POINTS)

    assert rec["ok"] and rec["points_mismatch"] is False
    assert [p["point"] for p in rec["points"]] == POINTS
    assert (rec["score_final"], rec["score_source"]) == (9, "code")


def test_eval_call_judge_falls_back_to_model_score_on_mismatch():
    bad = [{"i": 1, "status": "covered", "evidence": ""}]
    client = FakeClient(_raw(bad, score=5), _raw(bad, score=5))

    rec = call_judge(client, "m", 0, "prompt", fixed=POINTS)

    assert client.calls == 2 and rec["ok"] and rec["points_mismatch"] is True
    assert (rec["score_final"], rec["score_source"]) == (5, "model")


def test_eval_final_score_mismatch_never_uses_code():
    parsed = {"intent": "answer", "score": 4, "has_breakdown": True, "points_mismatch": True,
              "points": [{"status": "covered"}], "errors": []}
    assert final_score(parsed) == (4, "model")


def test_parse_stability_counts_marked_points_not_expected_points():
    def rec(n):
        return {"ok": True, "intent": "answer", "has_breakdown": True, "expected_points": POINTS,
                "points": [{"status": "covered"}] * n}
    assert parse_stability({"a": [rec(3), rec(4)]})["count_differs"] == 1.0


# ── bonus_points: судья, scoring, engine, eval ──────────────────────────────

BONUS = ["Jython без GIL", "C-расширения отпускают GIL"]
_ALL_COVERED = [{"i": n, "status": "covered", "evidence": ""} for n in (1, 2, 3)]
_TWO_OF_THREE = [{"i": 1, "status": "covered", "evidence": ""}, {"i": 2, "status": "covered", "evidence": ""},
                 {"i": 3, "status": "missing", "evidence": ""}]




@pytest.mark.parametrize("marks, flag, expected", [
    ([{"status": "covered"}, {"status": "missing"}], False, True),    # covered бонус → +1, флаг неважен
    ([{"status": "partial"}, {"status": "missing"}], True, False),    # разметка есть — флаг не используется
    ([], True, True),                                                 # разметки нет — fallback на флаг
    (None, False, False),
    ([{"status": "???"}], True, True),                                # битая разметка — fallback
])
def test_has_bonus(marks, flag, expected):
    assert has_bonus(marks, flag) is expected


def test_compute_score_bonus_marks_do_not_change_coverage():
    # D = 2/3 → 6; covered бонус → 7. Бонусные пункты в долю не входят.
    points = [{"status": "covered"}, {"status": "covered"}, {"status": "missing"}]
    assert compute_score(points, [], bonus=False, off_topic=False) == 6
    assert compute_score(points, [], bonus=False, off_topic=False,
                         bonus_marks=[{"status": "covered"}, {"status": "missing"}]) == 7
    assert compute_score(points, [], bonus=True, off_topic=False,
                         bonus_marks=[{"status": "missing"}, {"status": "missing"}]) == 6


def test_judge_lists_bonus_points_and_restores_marks(fake_judge):
    marks = [{"i": "B1", "status": "covered", "evidence": "Jython"}, {"i": "B2", "status": "missing", "evidence": ""}]
    structured = fake_judge(_evaluation(_ALL_COVERED, bonus_marks=marks))

    result = _judge(fixed_points=POINTS, bonus_points=BONUS)

    prompt = structured.calls[0][0].content
    assert f"B1. {BONUS[0]}" in prompt and f"B2. {BONUS[1]}" in prompt
    assert [m["point"] for m in result.bonus_marks] == BONUS and result.points_mismatch is False


def test_judge_bonus_mismatch_keeps_points_and_falls_back_to_flag(fake_judge):
    bad = [{"i": "B1", "status": "covered", "evidence": ""}]          # B2 пропущен
    structured = fake_judge(_evaluation(_TWO_OF_THREE, bonus_marks=bad, bonus=True),
                            _evaluation(_TWO_OF_THREE, bonus_marks=bad, bonus=True))

    result = _judge(fixed_points=POINTS, bonus_points=BONUS)

    assert len(structured.calls) == 2
    assert result.points_mismatch is False and result.bonus_marks == []
    assert [p["point"] for p in result.points] == POINTS
    assert Engine._answer_score(result) == 7                            # D=2/3 → 6, +1 по флагу модели


def test_judge_ignores_bonus_points_without_fixed_points(fake_judge):
    structured = fake_judge(_evaluation([{"point": "a", "status": "covered"}], expected_points=["a"]))

    _judge(bonus_points=BONUS)

    assert "Бонусные пункты (служебно; необязательны, в покрытие D не входят):\n(не заданы)" in \
        structured.calls[0][0].content


def test_answer_score_uses_bonus_marks_over_model_flag():
    analysis = AnswerEvaluation(intent="answer", feedback="", score=9, bonus=True, off_topic=False,
                                points=[{"status": "covered"}, {"status": "covered"}, {"status": "missing"}],
                                errors=[], bonus_marks=[{"status": "missing"}])
    assert Engine._answer_score(analysis) == 6


def test_eval_call_judge_scores_bonus_from_marks():
    marks = [{"i": "B1", "status": "missing", "evidence": ""}, {"i": "B2", "status": "covered", "evidence": ""}]
    raw = json.dumps(_evaluation(_TWO_OF_THREE, bonus_marks=marks), ensure_ascii=False)

    rec = call_judge(FakeClient(raw), "m", 0, "p", fixed=POINTS, bonus=BONUS)

    assert (rec["score_final"], rec["score_source"]) == (7, "code") and rec["bonus_mismatch"] is False


def test_eval_call_judge_bonus_mismatch_falls_back_to_flag():
    raw = json.dumps(_evaluation(_TWO_OF_THREE, bonus_marks=[], bonus=False), ensure_ascii=False)
    client = FakeClient(raw, raw)

    rec = call_judge(client, "m", 0, "p", fixed=POINTS, bonus=BONUS)

    assert client.calls == 2 and rec["bonus_mismatch"] is True and rec["points_mismatch"] is False
    assert (rec["score_final"], rec["score_source"]) == (6, "code")


def test_eval_build_prompt_fills_bonus_slot_only_with_fixed_points():
    template = "{fixed_points}|{bonus_points}"
    item = {**ITEM, "bonus_points": BONUS}
    assert build_prompt(template, item, "p", False) == \
        f"{format_fixed_points(POINTS)}|{format_fixed_points(BONUS, prefix='B')}"
    assert build_prompt(template, {**item, "key_points": None}, "p", False) == "(не заданы)|(не заданы)"
