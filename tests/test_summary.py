"""Агрегация итогов: пропуски, вердикт, индекс готовности, единые пороги."""
from __future__ import annotations

import json

import pytest

from sokrat.engine import build_report_view, report_markdown
from sokrat.gap_analysis import GapAnalysis
from sokrat.history import build_dynamics
from sokrat.interview_rules import should_stop_interview, verdict_for_score
from sokrat.readiness import compute_readiness
from sokrat.score_bands import SUMMARY_VERSION, band
from sokrat.summary_builder import compute_summary


def _entry(score: int, skills: list[str] | None = None, intent: str | None = None) -> dict:
    if intent is None:
        intent = "answer" if score > 0 else "skip"
    return {"score": score, "intent": intent, "skills_touched": list(skills or [])}


def _summary(scores: list[dict], skills: list[str] | None = None) -> dict:
    questions = [{"question": f"Вопрос {i + 1}"} for i in range(len(scores))]
    answers = ["ответ"] * len(scores)
    return compute_summary("Python Developer", "Middle", "technical", questions, answers, scores, skills)


# ── процент и счётчики ──────────────────────────────────────────────────────

def test_one_answer_and_six_skips_counts_all_questions():
    summary = _summary([_entry(9)] + [_entry(0)] * 6)

    assert summary["percentage"] == pytest.approx(12.9)
    assert summary["asked_count"] == 7
    assert summary["answered_count"] == 1
    assert summary["avg_score_answered"] == 9.0
    assert summary["skipped_count"] == 6
    assert summary["max_possible"] == 70
    assert summary["verdict"] == "Insufficient"
    assert summary["verdict_label"] == "Недостаточно ответов для вывода"
    assert summary["summary_version"] == SUMMARY_VERSION


def test_all_skipped_gives_zero_without_verdict():
    summary = _summary([_entry(0)] * 5)

    assert summary["percentage"] == 0.0
    assert summary["answered_count"] == 0
    assert summary["avg_score_answered"] == 0.0
    assert summary["verdict"] == "Insufficient"
    assert summary["recommendation"]


def test_empty_interview_does_not_divide_by_zero():
    summary = _summary([])

    assert summary["percentage"] == 0.0
    assert summary["verdict"] == "Insufficient"


def test_repeated_dont_know_counts_as_zero_in_denominator():
    summary = _summary([_entry(8)] * 3 + [_entry(0, intent="dont_know")])

    assert summary["percentage"] == pytest.approx(60.0)
    assert summary["answered_count"] == 3
    assert summary["verdict"] == "Competent"


# ── вердикт ─────────────────────────────────────────────────────────────────

def test_verdict_given_at_exactly_seventy_percent_answered():
    summary = _summary([_entry(10)] * 7 + [_entry(0)] * 3)

    assert summary["verdict"] == "Competent"


def test_verdict_withheld_below_seventy_percent_answered():
    summary = _summary([_entry(10)] * 6 + [_entry(0)] * 4)

    assert summary["verdict"] == "Insufficient"


@pytest.mark.parametrize(
    ("scores", "verdict"),
    [
        ([8] * 10, "Strong"),
        ([8] * 9 + [7], "Competent"),
        ([6] * 10, "Competent"),
        ([6] * 9 + [5], "Needs Work"),
    ],
)
def test_verdict_uses_shared_bands(scores, verdict):
    assert _summary([_entry(s) for s in scores])["verdict"] == verdict


# ── пороги ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("score", "expected"),
    [(0, "skipped"), (1, "needs_work"), (5, "needs_work"), (6, "good"),
     (7, "good"), (8, "excellent"), (10, "excellent")],
)
def test_band_and_question_verdict_agree(score, expected):
    assert band(score) == expected
    assert verdict_for_score(score)[0] == expected


def test_every_answered_question_lands_in_exactly_one_list():
    summary = _summary([_entry(5), _entry(6), _entry(7), _entry(8), _entry(0)])

    assert summary["strengths"] == ["Вопрос 4"]
    assert summary["improvements"] == ["Вопрос 1", "Вопрос 2", "Вопрос 3"]


# ── покрытие навыков ────────────────────────────────────────────────────────

def test_skill_without_questions_is_untested_and_ignored_in_skills_component():
    scores = [
        _entry(8, ["Python"]),
        _entry(0, ["SQL"], intent="dont_know"),
    ] + [_entry(6)] * 6
    summary = _summary(scores, ["Python", "SQL", "Docker"])

    coverage = {e["skill"]: e for e in summary["skills_coverage"]}
    assert coverage["SQL"]["tested"] is True
    assert coverage["SQL"]["avg_score"] == 0.0
    assert coverage["Docker"]["tested"] is False

    readiness = compute_readiness(summary)
    skills = next(c for c in readiness["components"] if c["key"] == "skills")
    assert skills["value"] == pytest.approx(40.0)
    assert readiness["untested_skills"] == ["Docker"]


# ── индекс готовности ───────────────────────────────────────────────────────

def test_fewer_than_eight_answers_gives_range_instead_of_number():
    summary = _summary([_entry(5)] * 2 + [_entry(0)] * 7)

    readiness = compute_readiness(summary, min_answers=8)

    assert readiness["preliminary"] is True
    assert readiness["index"] is None
    assert readiness["label"] is None
    assert readiness["answers_needed"] == 6
    assert readiness["range"] == [5, 50]


def test_all_skipped_gives_full_range():
    readiness = compute_readiness(_summary([_entry(0)] * 4), min_answers=8)

    assert readiness["preliminary"] is True
    assert readiness["range"][0] == 0


def test_eight_answers_give_number_rounded_to_five_with_label():
    summary = _summary([_entry(8)] * 7 + [_entry(7)])

    readiness = compute_readiness(summary, min_answers=8)

    assert readiness["preliminary"] is False
    assert readiness["precision"] == "normal"
    assert readiness["index"] == 80
    assert readiness["label"] == "Готов к собеседованию"


def test_short_interview_all_answered_gives_rough_index_without_label():
    summary = _summary([_entry(8)] * 5 + [_entry(7)])

    readiness = compute_readiness(summary, min_answers=6)

    assert readiness["preliminary"] is False
    assert readiness["precision"] == "rough"
    assert readiness["index"] == 80
    assert readiness["label"] is None
    assert readiness["answered_count"] == 6


def test_resume_is_reported_separately_not_in_index():
    summary = _summary([_entry(10, ["Python"])] * 8, ["Python"])
    gaps = GapAnalysis(matched=["Python"], missing=["Go"])

    readiness = compute_readiness(summary, gaps)

    assert readiness["index"] == 100
    assert readiness["resume_coverage_pct"] == 50.0
    assert [c["key"] for c in readiness["components"]] == ["performance", "skills"]


def test_performance_and_skills_weights_are_renormalized():
    summary = _summary([_entry(10)] * 8 + [_entry(4, ["Python"])], ["Python"])

    readiness = compute_readiness(summary)

    # P = 84/90 = 93.3, S = 40 → 0.53·93.3 + 0.47·40 = 68.3 → 70
    assert readiness["index"] == 70
    assert sum(c["weight_pct"] for c in readiness["components"]) == 100


# ── ранняя остановка ────────────────────────────────────────────────────────

def test_no_early_stop_before_eight_questions():
    strong = [_entry(9)] * 7

    assert should_stop_interview(6, 6, 10, strong[:6], []) is False
    assert should_stop_interview(7, 6, 10, strong, []) is False
    assert should_stop_interview(8, 6, 10, strong + [_entry(9)], []) is True


def test_short_mode_runs_to_its_max():
    strong = [_entry(9)] * 6

    assert should_stop_interview(4, 4, 6, strong[:4], []) is False
    assert should_stop_interview(6, 4, 6, strong, []) is True


# ── совместимость и динамика ────────────────────────────────────────────────

_OLD_SUMMARY = {
    "avg_score": 7.0,
    "total_score": 14,
    "max_possible": 20,
    "percentage": 70.0,
    "verdict": "Competent",
    "counted_count": 2,
    "skipped_count": 1,
    "recommendation": "…",
    "skills_coverage": [{"skill": "Python", "questions_count": 0, "avg_score": 0.0}],
    "dimensions": {},
    "readiness": {"index": 70, "label": "Почти готов",
                  "components": [{"key": "performance", "name": "P", "value": 70.0, "weight_pct": 100}]},
}


def _old_session_data() -> dict:
    return {
        "session_id": "old",
        "role": "Python Developer",
        "questions": [{"question": "a"}, {"question": "b"}, {"question": "c"}],
        "answers": ["x", "y", ""],
        "scores": [{"score": 7}, {"score": 7}, {"score": 0}],
        "summary": _OLD_SUMMARY,
    }


def test_old_session_report_still_builds():
    view = build_report_view(_old_session_data())

    assert view["summary_version"] == 1
    assert view["asked_count"] == 3
    assert view["answered_count"] == 2
    assert view["readiness"]["index"] == 70
    assert "70/100" in report_markdown(view)


def test_new_session_markdown_shows_answered_and_range():
    summary = _summary([_entry(6)] * 2 + [_entry(0)] * 7)
    summary["readiness"] = compute_readiness(summary, min_answers=8)
    data = {"questions": [{"question": "q"}] * 9, "answers": [""] * 9,
            "scores": [_entry(6)] * 2 + [_entry(0)] * 7, "summary": summary}

    text = report_markdown(build_report_view(data))

    assert "Отвечено 2 из 9" in text
    assert "Ответь ещё на 6" in text
    assert "Недостаточно ответов для вывода" in text


def _write_session(path, session_id: str, pct: float, version: int | None) -> None:
    summary = {"percentage": pct, "counted_count": 5}
    if version is not None:
        summary["summary_version"] = version
    path.joinpath(f"{session_id}.json").write_text(json.dumps({
        "session_id": session_id, "user_id": "u", "role": "Python Developer",
        "finished_at": f"2026-10-0{len(session_id)}T00:00:00", "summary": summary,
    }), encoding="utf-8")


def test_dynamics_compares_only_same_formula_version(tmp_path):
    _write_session(tmp_path, "a", 90.0, None)
    _write_session(tmp_path, "bb", 40.0, SUMMARY_VERSION)
    current = {"percentage": 50.0, "summary_version": SUMMARY_VERSION}

    dyn = build_dynamics(current, "Python Developer", "now", tmp_path, "u")

    assert dyn["points"] == [40.0, 50.0]
    assert dyn["sessions_count"] == 2


def test_dynamics_is_empty_when_only_old_formula_sessions_exist(tmp_path):
    _write_session(tmp_path, "a", 90.0, None)
    current = {"percentage": 50.0, "summary_version": SUMMARY_VERSION}

    assert build_dynamics(current, "Python Developer", "now", tmp_path, "u") is None


def test_round_to_rounds_half_up():
    from sokrat.score_bands import round_to

    assert round_to(62.5, 5) == 65
    assert round_to(75, 10) == 80
    assert round_to(74.9, 10) == 70


def test_other_vacancies_ignore_old_formula_sessions(tmp_path):
    from sokrat.history import other_vacancies

    for sid, version in (("a", None), ("bb", SUMMARY_VERSION)):
        summary = {"percentage": 70.0, "counted_count": 8, "readiness": {"index": 70}}
        if version:
            summary["summary_version"] = version
        tmp_path.joinpath(f"{sid}.json").write_text(json.dumps({
            "session_id": sid, "user_id": "u", "role": "Dev",
            "finished_at": f"2026-10-0{len(sid)}T00:00:00",
            "job_vacancy": {"company": f"Co-{sid}"}, "summary": summary,
        }), encoding="utf-8")

    assert [o["company"] for o in other_vacancies("u", "Other", "now", tmp_path)] == ["Co-bb"]


# ── ожидаемая длина интервью (счётчик «Вопрос N из ~M») ─────────────────────

@pytest.mark.parametrize(
    ("min_q", "max_q", "expected"),
    [(4, 6, 6), (6, 10, 8), (8, 14, 8), (10, 18, 10), (14, 20, 14), (5, 5, 5)],
)
def test_earliest_stop_matches_stop_rule(min_q, max_q, expected):
    from sokrat.interview_rules import earliest_stop

    strong = [_entry(9)] * max_q

    assert earliest_stop(min_q, max_q) == expected
    assert should_stop_interview(expected - 1, min_q, max_q, strong[: expected - 1], []) is False


def test_question_view_exposes_expected_length():
    from sokrat.engine import Engine, Interview

    iv = Interview(id="x", min_q=6, max_q=10, questions=[{"question": "q"}])

    view = Engine._question_view(Engine.__new__(Engine), iv)

    assert view["expected"] == 8
    assert view["max"] == 10
