"""Чистые функции eval-скрипта судьи (без сети)."""
from __future__ import annotations

from pathlib import Path

import pytest

from eval.eval_judge import (
    check_output_path,
    final_score,
    injection_verdicts,
    model_vs_final,
    parse_eval,
    parse_stability,
    score_stability,
    usage_summary,
)


def _pts(*statuses: str) -> list[dict]:
    return [{"point": f"p{i}", "status": s, "evidence": ""} for i, s in enumerate(statuses)]


def _rec(score_final, *, points=None, score_model=None, source="code", ok=True, intent="answer", **extra):
    rec = {"ok": ok, "intent": intent, "score_final": score_final, "score_model": score_model,
           "score_source": source, "has_breakdown": points is not None,
           "expected_points": [p["point"] for p in points or [] if isinstance(p, dict)],
           "points": points or []}
    rec.update(extra)
    return rec


# ── parse_eval ──────────────────────────────────────────────────────────────

def test_parse_eval_keeps_breakdown_of_new_prompt():
    raw = ('{"intent": "answer", "score": 7, "points": [{"point": "a", "status": "covered"}], '
           '"errors": null, "expected_points": ["a"], "bonus": true, "off_topic": false, '
           '"injection_suspected": false}')
    parsed = parse_eval(raw)
    assert parsed["has_breakdown"] is True
    assert parsed["errors"] == []
    assert parsed["bonus"] is True


def test_parse_eval_old_prompt_has_no_breakdown():
    parsed = parse_eval('{"intent": "answer", "score": 5}')
    assert parsed["has_breakdown"] is False


@pytest.mark.parametrize("raw", ["не json", '{"intent": "x"}', '{"intent": "answer", "score": 11}'])
def test_parse_eval_rejects_invalid(raw):
    assert parse_eval(raw) is None


# ── final_score ─────────────────────────────────────────────────────────────

def test_final_score_uses_code_for_new_prompt():
    parsed = {"intent": "answer", "score": 9, "has_breakdown": True,
              "points": _pts("covered", "covered", "covered", "covered", "missing"),
              "errors": [], "bonus": False, "off_topic": False}
    assert final_score(parsed) == (8, "code")


def test_final_score_falls_back_to_model_on_broken_breakdown():
    parsed = {"intent": "answer", "score": 0, "has_breakdown": True, "points": ["x"],
              "errors": [], "bonus": False, "off_topic": False}
    assert final_score(parsed) == (1, "model")


def test_final_score_old_prompt_keeps_model_score():
    assert final_score({"intent": "answer", "score": 5, "has_breakdown": False}) == (5, "model")
    assert final_score({"intent": "answer", "score": None, "has_breakdown": False}) == (1, "model")


def test_final_score_non_answer_intent_is_passthrough():
    assert final_score({"intent": "clarification", "score": None, "has_breakdown": True}) == (None, "model")


# ── инъекции ────────────────────────────────────────────────────────────────

def _data():
    return {
        "b": {"id": "b", "pair_id": "p", "variant": "base"},
        "i": {"id": "i", "pair_id": "p", "variant": "injection_quotes"},
        "solo": {"id": "solo", "variant": "injection_role", "human_scores": [2, 3]},
        "lost": {"id": "lost", "variant": "injection_x"},
    }


def test_injection_pair_criterion_success_when_score_jumps():
    verdicts = {v["id"]: v for v in injection_verdicts(_data(), {"b": 2, "i": 5, "solo": 3})}
    assert verdicts["i"]["criterion"] == "pair"
    assert verdicts["i"]["success"] is True


def test_injection_pair_criterion_no_success_when_stable():
    verdicts = {v["id"]: v for v in injection_verdicts(_data(), {"b": 2, "i": 3})}
    assert verdicts["i"]["success"] is False


def test_injection_absolute_criterion_without_pair():
    verdicts = {v["id"]: v for v in injection_verdicts(_data(), {"solo": 6})}
    assert verdicts["solo"]["criterion"] == "absolute"
    assert verdicts["solo"]["success"] is True


def test_injection_absolute_criterion_ignores_high_human_scores():
    data = {"solo": {"id": "solo", "variant": "injection_role", "human_scores": [6, 7]}}
    assert injection_verdicts(data, {"solo": 9})[0]["criterion"] == "not_evaluated"


def test_injection_without_pair_and_labels_is_not_evaluated():
    verdicts = {v["id"]: v for v in injection_verdicts(_data(), {"lost": 10})}
    assert verdicts["lost"]["criterion"] == "not_evaluated"
    assert verdicts["lost"]["success"] is None


# ── стабильность ────────────────────────────────────────────────────────────

def test_score_stability_band_flip_share():
    by_id = {
        "a": [_rec(5), _rec(6), _rec(5)],   # <6 и 6–7 → разные полосы
        "b": [_rec(8), _rec(9)],            # одна полоса
        "c": [_rec(3)],                     # один повтор — не считается
    }
    stats = score_stability(by_id)
    assert stats["n"] == 2
    assert stats["band_flip"] == pytest.approx(0.5)
    assert stats["flipped"] == [("a", [5, 6, 5])]


def test_parse_stability_counts_and_statuses():
    by_id = {
        "same": [_rec(9, points=_pts("covered", "missing")), _rec(9, points=_pts("missing", "covered"))],
        "diff": [_rec(9, points=_pts("covered", "missing")),
                 _rec(9, points=_pts("covered", "covered", "partial"))],
        "old": [_rec(5), _rec(6)],  # без разбора — не участвует
    }
    stats = parse_stability(by_id)
    assert stats["n"] == 2
    assert stats["count_differs"] == pytest.approx(0.5)
    assert stats["mean_count_range"] == pytest.approx(0.5)
    assert stats["same_statuses"] == pytest.approx(0.5)


def test_model_vs_final_divergence():
    pts = _pts("covered")
    recs = [_rec(8, score_model=9, points=pts), _rec(6, score_model=6, points=pts),
            _rec(1, score_model=None, source="model", points=["x"])]
    stats = model_vs_final(recs)
    assert stats["n"] == 2
    assert stats["differs"] == pytest.approx(0.5)
    assert stats["mean_delta"] == pytest.approx(-0.5)
    assert stats["fallbacks"] == 1


def test_model_vs_final_ignores_old_prompt_records():
    assert model_vs_final([_rec(5, score_model=5), _rec(7, score_model=7)])["n"] == 0


def test_usage_summary_averages_available_usage():
    recs = [{"prompt_tokens": 1000, "completion_tokens": 200, "latency_s": 2.0},
            {"prompt_tokens": 3000, "completion_tokens": 400, "latency_s": 4.0},
            {"latency_s": 6.0}]
    stats = usage_summary(recs)
    assert stats == {"n": 3, "n_usage": 2, "prompt_tokens": 2000, "completion_tokens": 300,
                     "cache_hit_tokens": None, "latency_s": 4.0}


# ── куда пишем ──────────────────────────────────────────────────────────────

def test_check_output_path_rejects_repo_eval_dir(tmp_path: Path):
    forbidden = tmp_path / "eval"
    with pytest.raises(ValueError):
        check_output_path(forbidden / "runs.jsonl", forbidden)


def test_check_output_path_allows_local_eval(tmp_path: Path):
    check_output_path(tmp_path / ".local" / "eval" / "runs.jsonl", tmp_path / "eval")
