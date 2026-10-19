"""Фиксированные key_points: схема, скрипт генерации, генератор вопросов на лету (без сети)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import generate_key_points as gkp
from sokrat import question_generator as qg_mod
from sokrat.key_points import (
    apply_bonus_points,
    apply_fixed_points,
    format_fixed_points,
    validate_bonus_points,
    validate_key_points,
)

POINTS = ["GIL пускает исполнять байткод одному потоку", "CPU-bound потоками не ускорить",
          "для IO-bound потоки помогают"]


# ── схема ───────────────────────────────────────────────────────────────────

def test_validate_key_points_accepts_three_to_six_and_trims_spaces():
    assert validate_key_points([" a ", "b  c", "d"]) == ["a", "b c", "d"]
    assert validate_key_points([f"p{i}" for i in range(6)]) == [f"p{i}" for i in range(6)]


@pytest.mark.parametrize("value", [
    ["a", "b"],                                   # меньше 3
    [f"p{i}" for i in range(7)],                  # больше 6
    ["a", "b", " "],                              # пустая строка
    ["a", "B", "b "],                             # дубль без учёта регистра и пробелов
    ["a", "b", 3],                                # не строка
    "a; b; c",                                    # не список
    None,
])
def test_validate_key_points_rejects_bad_lists(value):
    assert validate_key_points(value) is None


def test_validate_bonus_points_allows_zero_to_three_distinct_from_key_points():
    assert validate_bonus_points(None, POINTS) == []
    assert validate_bonus_points([], POINTS) == []
    assert validate_bonus_points(["x", "y", "z"], POINTS) == ["x", "y", "z"]


@pytest.mark.parametrize("value", [["a", "b", "c", "d"], ["x", "x"], [POINTS[0].upper()], "x", ["x", ""]])
def test_validate_bonus_points_rejects_bad_lists(value):
    assert validate_bonus_points(value, POINTS) is None


def test_format_fixed_points_numbers_points_or_says_not_set():
    assert format_fixed_points(["a", "b", "c"]) == "1. a\n2. b\n3. c"
    assert format_fixed_points(None) == "(не заданы)"


def test_apply_fixed_points_orders_by_index_and_restores_text():
    marked = [{"i": 2, "status": "partial", "evidence": "x"},
              {"i": "1", "status": "covered", "evidence": "y"},
              {"i": 3, "status": "missing", "evidence": ""}]
    assert apply_fixed_points(marked, POINTS) == [
        {"point": POINTS[0], "status": "covered", "evidence": "y"},
        {"point": POINTS[1], "status": "partial", "evidence": "x"},
        {"point": POINTS[2], "status": "missing", "evidence": ""},
    ]


@pytest.mark.parametrize("marked", [
    [{"i": 1, "status": "covered"}, {"i": 2, "status": "covered"}],                      # пропущен пункт
    [{"i": 1, "status": "covered"}, {"i": 1, "status": "covered"}, {"i": 3, "status": "missing"}],
    [{"i": 1, "status": "covered"}, {"i": 2, "status": "covered"}, {"i": 4, "status": "missing"}],
    [{"point": "a", "status": "covered"}] * 3,                                           # без номеров
    [{"i": True, "status": "covered"}, {"i": 2, "status": "covered"}, {"i": 3, "status": "missing"}],
])
def test_apply_fixed_points_rejects_unmatched_markup(marked):
    assert apply_fixed_points(marked, POINTS) is None

BONUS = ["Jython без GIL", "C-расширения отпускают GIL"]
def test_apply_bonus_points_accepts_b_prefix_and_plain_numbers():
    marks = [{"i": "B2", "status": "missing"}, {"i": 1, "status": "covered", "evidence": "e"}]
    assert apply_bonus_points(marks, BONUS) == [
        {"point": BONUS[0], "status": "covered", "evidence": "e"},
        {"point": BONUS[1], "status": "missing", "evidence": ""},
    ]
    assert apply_bonus_points([{"i": "B1", "status": "covered"}], BONUS) is None


# ── скрипт generate_key_points ──────────────────────────────────────────────

def _bank(tmp_path: Path) -> Path:
    db = {"roles": {"Python Developer": {"Junior": {
        "technical": [
            {"id": "empty", "question": "Что такое GIL?", "hints": ["потоки"], "ideal_keywords": ["GIL"]},
            {"id": "filled", "question": "q2", "key_points": ["a", "b", "c"], "key_points_source": "llm"},
            {"id": "human", "question": "q3", "key_points": ["x"], "key_points_source": "human"},
        ],
        "hr": [{"id": "hr1", "question": "Расскажи о конфликте", "hints": [], "ideal_keywords": []}],
    }}}}
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class FakeLlm:
    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


GOOD = json.dumps({"key_points": ["п1", "п2", "п3", "п4"]}, ensure_ascii=False)


def test_script_fills_only_empty_questions_and_keeps_other_fields(tmp_path):
    path = _bank(tmp_path)
    llm = FakeLlm(GOOD)

    summary = gkp.run(path, llm)

    db = json.loads(path.read_text(encoding="utf-8"))
    tech = db["roles"]["Python Developer"]["Junior"]["technical"]
    assert tech[0]["key_points"] == ["п1", "п2", "п3", "п4"]
    assert tech[0]["key_points_source"] == "llm"
    assert tech[0]["hints"] == ["потоки"] and tech[0]["ideal_keywords"] == ["GIL"]
    assert tech[1]["key_points"] == ["a", "b", "c"]                       # заполненный не тронут
    assert tech[2] == {"id": "human", "question": "q3", "key_points": ["x"], "key_points_source": "human"}
    assert db["roles"]["Python Developer"]["Junior"]["hr"][0]["key_points_source"] == "llm"
    assert len(llm.prompts) == 2 and summary.already_filled == 2
    assert "Что такое GIL?" in llm.prompts[0] and "Junior" in llm.prompts[0]


def test_script_writes_bonus_points_and_defaults_to_empty(tmp_path):
    path = _bank(tmp_path)
    with_bonus = json.dumps({"key_points": ["п1", "п2", "п3"], "bonus_points": ["деталь"]}, ensure_ascii=False)

    gkp.run(path, FakeLlm(with_bonus, GOOD))

    db = json.loads(path.read_text(encoding="utf-8"))["roles"]["Python Developer"]["Junior"]
    assert db["technical"][0]["bonus_points"] == ["деталь"]
    assert db["hr"][0]["bonus_points"] == []                              # поле не пришло → []


def test_script_retries_when_bonus_duplicates_key_point(tmp_path):
    path = _bank(tmp_path)
    dup = json.dumps({"key_points": ["п1", "п2", "п3"], "bonus_points": ["п1"]}, ensure_ascii=False)
    llm = FakeLlm(dup, GOOD)

    summary = gkp.run(path, llm, ids={"empty"})

    assert len(llm.prompts) == 2 and summary.generated[0][1].bonus_points == []


def test_script_parallel_workers_fill_all_and_count_calls(tmp_path):
    path = _bank(tmp_path)
    llm = FakeLlm(GOOD)

    summary = gkp.run(path, llm, workers=4)

    ids = [r.question["id"] for r, _ in summary.generated]
    assert ids == ["empty", "hr1"] and summary.llm_calls == 2       # порядок сохранён
    db = json.loads(path.read_text(encoding="utf-8"))["roles"]["Python Developer"]["Junior"]
    assert db["technical"][0]["key_points"] and db["hr"][0]["key_points"]


def test_script_second_run_is_noop(tmp_path):
    path = _bank(tmp_path)
    gkp.run(path, FakeLlm(GOOD))
    before = path.read_text(encoding="utf-8")
    llm = FakeLlm(GOOD)

    summary = gkp.run(path, llm)

    assert llm.prompts == [] and summary.generated == []
    assert path.read_text(encoding="utf-8") == before


def test_script_retries_invalid_answer_then_skips(tmp_path):
    path = _bank(tmp_path)
    before = path.read_text(encoding="utf-8")
    llm = FakeLlm('{"key_points": ["один", "два"]}')                      # всегда 2 пункта — невалидно

    summary = gkp.run(path, llm, ids={"empty"})

    assert len(llm.prompts) == gkp.ATTEMPTS
    assert summary.skipped_invalid == ["empty"]
    assert path.read_text(encoding="utf-8") == before


def test_script_retry_succeeds_on_second_attempt(tmp_path):
    path = _bank(tmp_path)
    llm = FakeLlm("not json", GOOD)

    summary = gkp.run(path, llm, ids={"empty"})

    assert len(llm.prompts) == 2 and [r.question["id"] for r, _ in summary.generated] == ["empty"]


def test_script_dry_run_does_not_write_and_limit_caps_calls(tmp_path):
    path = _bank(tmp_path)
    before = path.read_text(encoding="utf-8")
    llm = FakeLlm(GOOD)

    summary = gkp.run(path, llm, limit=1, dry_run=True)

    assert len(llm.prompts) == 1 and len(summary.generated) == 1
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("question, interview_type", [
    ("Почему ты хочешь быть Product Manager, а не разработчиком?", "hr"),
    ("Почему ты выбрал Python как основной язык?", "hr"),
    ("Почему ты хочешь работать в нашей компании?", "hr"),
    ("Где ты видишь себя через 2 года?", "mixed"),
    ("Какие у тебя карьерные цели?", "hr"),
    ("Что тебя мотивирует в работе разработчика?", "hr"),
])
def test_is_motivational_detects_motivation_questions(question, interview_type):
    assert gkp.is_motivational(question, interview_type)


@pytest.mark.parametrize("question, interview_type", [
    ("Расскажи о техническом решении. Почему ты выбрал именно такой подход?", "mixed"),  # про решение
    ("Как ты поддерживаешь мотивацию инженеров в команде?", "hr"),                       # управленческий
    ("Расскажи о случае, когда ты получил критику на свой код.", "hr"),                   # поведенческий
    ("Почему ты хочешь использовать asyncio, а не потоки?", "technical"),                  # не hr/mixed
])
def test_is_motivational_ignores_other_questions(question, interview_type):
    assert not gkp.is_motivational(question, interview_type)


def test_script_prompt_tells_model_question_kind(tmp_path):
    path = _bank(tmp_path)
    db = json.loads(path.read_text(encoding="utf-8"))
    db["roles"]["Python Developer"]["Junior"]["hr"][0]["question"] = "Почему ты хочешь быть разработчиком?"
    path.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
    llm = FakeLlm(GOOD)

    gkp.run(path, llm, dry_run=True)

    assert gkp.KIND_OTHER in llm.prompts[0] and gkp.KIND_MOTIVATIONAL in llm.prompts[1]


# ── генератор вопросов на лету ──────────────────────────────────────────────

def test_generated_questions_keep_valid_key_points_and_drop_invalid(monkeypatch):
    items = [
        {"question": "Что такое GIL?", "hints": ["h"], "ideal_keywords": ["GIL"], "key_points": ["a", "b", "c"]},
        {"question": "Что такое asyncio?", "hints": [], "ideal_keywords": [], "key_points": ["только один"]},
    ]
    model = SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content=json.dumps(items, ensure_ascii=False)))
    monkeypatch.setattr(qg_mod, "get_chat_model_for", lambda *a, **k: model)

    out = qg_mod.generate_questions("Python Developer", "Junior", "technical", 2, [])

    assert out[0]["key_points"] == ["a", "b", "c"] and out[0]["key_points_source"] == "llm"
    assert "key_points" not in out[1] and "key_points_source" not in out[1]
