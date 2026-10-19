"""Детерминированный score по процедуре analyzer.txt п.2.4."""
from __future__ import annotations

import pytest

from sokrat.scoring import compute_score


def _points(covered: int = 0, partial: int = 0, missing: int = 0) -> list[dict]:
    statuses = ["covered"] * covered + ["partial"] * partial + ["missing"] * missing
    return [{"point": f"p{i}", "status": s, "evidence": ""} for i, s in enumerate(statuses)]


def _error(core: bool) -> dict:
    return {"quote": "q", "issue": "i", "core": core}


# ── а) не по теме ───────────────────────────────────────────────────────────

def test_off_topic_gives_one_regardless_of_points():
    assert compute_score(_points(covered=3), [], bonus=True, off_topic=True) == 1


def test_off_topic_gives_one_even_without_points():
    assert compute_score([], [], bonus=False, off_topic=True) == 1


# ── б) критическая ошибка ───────────────────────────────────────────────────

def test_core_error_without_covered_gives_two():
    assert compute_score(_points(partial=2, missing=1), [_error(True)], bonus=True, off_topic=False) == 2


def test_core_error_with_covered_gives_three_and_ignores_bonus_and_minor_errors():
    errors = [_error(True), _error(False), _error(False)]
    assert compute_score(_points(covered=3), errors, bonus=True, off_topic=False) == 3


# ── в) покрытие D ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("covered", "partial", "missing", "expected"),
    [
        (0, 0, 4, 2),    # D = 0
        (0, 1, 3, 3),    # D = 0.125
        (1, 0, 4, 3),    # D = 0.2
        (0, 3, 2, 4),    # D = 0.3 — нижняя граница 4
        (1, 1, 2, 4),    # D = 0.375
        (1, 0, 1, 5),    # D = 0.5 — нижняя граница 5
        (3, 0, 2, 5),    # D = 0.6
        (2, 1, 1, 5),    # D = 0.625
        (13, 0, 7, 6),   # D = 0.65 — нижняя граница 6
        (3, 0, 1, 6),    # D = 0.75
        (4, 0, 1, 8),    # D = 0.8 — нижняя граница 8
        (3, 1, 0, 8),    # D = 0.875
        (4, 0, 0, 9),    # D = 1
    ],
)
def test_coverage_bands(covered, partial, missing, expected):
    assert compute_score(_points(covered, partial, missing), [], bonus=False, off_topic=False) == expected


# ── г) бонус ────────────────────────────────────────────────────────────────

def test_bonus_turns_six_into_seven():
    assert compute_score(_points(covered=3, missing=1), [], bonus=True, off_topic=False) == 7


def test_bonus_with_full_coverage_and_no_errors_gives_ten():
    assert compute_score(_points(covered=4), [], bonus=True, off_topic=False) == 10


def test_ten_is_impossible_with_minor_errors():
    assert compute_score(_points(covered=4), [_error(False)], bonus=True, off_topic=False) == 9


# ── д) некритичные ошибки ───────────────────────────────────────────────────

def test_each_minor_error_subtracts_one():
    errors = [_error(False), _error(False)]
    assert compute_score(_points(covered=4), errors, bonus=False, off_topic=False) == 7


def test_minor_errors_never_go_below_two():
    errors = [_error(False)] * 5
    assert compute_score(_points(covered=1, missing=3), errors, bonus=False, off_topic=False) == 2


# ── битый вход → None (вызывающий берёт балл модели) ────────────────────────

def test_empty_points_returns_none():
    assert compute_score([], [], bonus=False, off_topic=False) is None


@pytest.mark.parametrize(
    "points",
    [
        [{"point": "p", "status": "done", "evidence": ""}],
        [{"point": "p", "evidence": ""}],
        ["covered"],
    ],
)
def test_malformed_points_return_none(points):
    assert compute_score(points, [], bonus=False, off_topic=False) is None


@pytest.mark.parametrize("errors", [["ошибка"], [{"quote": "q", "issue": "i", "core": "false"}]])
def test_malformed_errors_return_none(errors):
    assert compute_score(_points(covered=2), errors, bonus=False, off_topic=False) is None


def test_missing_flags_are_treated_as_false():
    assert compute_score(_points(covered=3, missing=1), [], bonus=None, off_topic=None) == 6


def test_status_is_case_and_space_insensitive():
    points = [{"point": "p", "status": " Covered ", "evidence": "x"}]
    assert compute_score(points, [], bonus=False, off_topic=False) == 9
