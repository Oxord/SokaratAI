"""scripts/review_key_points.py на моках (без сети)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import review_key_points as rkp

KP = ["п1", "п2", "п3"]


def _bank(tmp_path: Path) -> Path:
    db = {"roles": {"Python Developer": {"Junior": {"technical": [
        {"id": "llm", "question": "q1", "key_points": KP, "bonus_points": ["b1"], "key_points_source": "llm"},
        {"id": "human", "question": "q2", "key_points": KP, "key_points_source": "human"},
        {"id": "nopoints", "question": "q3"},
    ]}}}}
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
    return path


def _flag(point="2", severity="высокая", criterion=1):
    return {"point": point, "criterion": criterion, "problem": "p", "suggestion": "s", "severity": severity}


class FakeLlm:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def _raw(*flags):
    return json.dumps({"flags": list(flags)}, ensure_ascii=False)


def test_review_writes_only_flags_and_keeps_points(tmp_path):
    path = _bank(tmp_path)
    before = json.loads(path.read_text(encoding="utf-8"))
    llm = FakeLlm(_raw(_flag(), _flag("B1", "низкая", 2), _flag("missing", "средняя", 3)))

    summary = rkp.run(path, llm, "РОЛЬ")

    after = json.loads(path.read_text(encoding="utf-8"))
    q = after["roles"]["Python Developer"]["Junior"]["technical"][0]
    assert [f["point"] for f in q["key_points_flags"]] == ["2", "B1", "missing"]
    q.pop("key_points_flags")
    assert after == before                                          # больше ничего не изменилось
    assert len(llm.prompts) == 1 and summary.skipped_human == 1 and summary.skipped_no_points == 1
    assert llm.prompts[0].startswith("РОЛЬ") and "1. п1" in llm.prompts[0] and "B1. b1" in llm.prompts[0]


def test_review_second_run_is_noop_and_force_rechecks(tmp_path):
    path = _bank(tmp_path)
    rkp.run(path, FakeLlm(_raw()), "r")
    llm = FakeLlm(_raw())

    assert rkp.run(path, llm, "r").skipped_done == 1 and llm.prompts == []
    assert len(rkp.run(path, llm, "r", force=True).reviewed) == 1


def test_review_include_human(tmp_path):
    llm = FakeLlm(_raw())
    rkp.run(_bank(tmp_path), llm, "r", include_human=True)
    assert len(llm.prompts) == 2


def test_review_retries_invalid_then_skips_without_writing(tmp_path):
    path = _bank(tmp_path)
    before = path.read_text(encoding="utf-8")
    llm = FakeLlm(_raw(_flag(point="7")))                            # пункта 7 нет

    summary = rkp.run(path, llm, "r")

    assert len(llm.prompts) == rkp.ATTEMPTS and summary.skipped_invalid == ["llm"]
    assert path.read_text(encoding="utf-8") == before


def test_review_dry_run_does_not_write(tmp_path):
    path = _bank(tmp_path)
    before = path.read_text(encoding="utf-8")
    rkp.run(path, FakeLlm(_raw(_flag())), "r", dry_run=True)
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("raw, ok", [
    ('```json\n{"flags": []}\n```', True),                           # markdown-обёртка
    (_raw(_flag(criterion="3")), True),                                # критерий строкой
    (_raw(_flag(severity="критичная")), False),
    (_raw(_flag(criterion=9)), False),
    (_raw(_flag(point="B2")), False),                                  # бонусный пункт один
    ('{"items": []}', False),
    ("не json", False),
])
def test_parse_flags_validates_format(raw, ok):
    assert (rkp.parse_flags(raw, n_key=3, n_bonus=1) is not None) is ok


def _bank_n(tmp_path: Path, n: int) -> Path:
    qs = [{"id": f"q{i}", "question": f"вопрос {i}", "key_points": KP, "bonus_points": ["b1"],
           "key_points_source": "llm"} for i in range(n)]
    qs.append({"id": "human", "question": "h", "key_points": KP, "key_points_source": "human"})
    path = tmp_path / "questions.json"
    path.write_text(json.dumps({"roles": {"R": {"Junior": {"technical": qs}}}}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def test_export_batches_splits_and_makes_self_contained_prompts(tmp_path):
    bank, out = _bank_n(tmp_path, 5), tmp_path / "batches"

    paths, summary = rkp.export_batches(bank, "РОЛЬ РЕЦЕНЗЕНТА", out, batch_size=2)

    assert [p.name for p in paths] == ["batch_01.md", "batch_02.md", "batch_03.md"]
    assert json.loads((out / "batch_03.ids.json").read_text(encoding="utf-8")) == ["q4"]
    text = paths[0].read_text(encoding="utf-8")
    assert text.startswith("РОЛЬ РЕЦЕНЗЕНТА") and "id: q0" in text and "id: q1" in text
    assert "ТОЛЬКО один JSON-объект" in text and "(2 шт.)" in text and "B1. b1" in text
    assert summary.skipped_human == 1


def test_import_batches_writes_flags_and_reports_bad_items(tmp_path):
    bank, out = _bank_n(tmp_path, 3), tmp_path / "batches"
    rkp.export_batches(bank, "r", out, batch_size=2)
    reply = {"reviews": [{"id": "q0", "flags": [_flag()]},
                         {"id": "q1", "flags": [_flag(point="9")]},      # невалидный пункт
                         {"id": "чужой", "flags": []}]}
    (out / "batch_01.response.json").write_text("```json\n" + json.dumps(reply, ensure_ascii=False) + "\n```",
                                                encoding="utf-8")

    summary, errors = rkp.import_batches(bank, out)

    qs = json.loads(bank.read_text(encoding="utf-8"))["roles"]["R"]["Junior"]["technical"]
    assert qs[0]["key_points_flags"][0]["point"] == "2"
    assert "key_points_flags" not in qs[1] and "key_points_flags" not in qs[2]
    assert summary.skipped_invalid == ["q1"]
    assert any("q1: флаги не по формату" in e for e in errors)
    assert any("'чужой'" in e for e in errors)
    assert any("batch_02.response.json: ответа нет" in e for e in errors)


def test_import_batches_reports_missing_ids(tmp_path):
    bank, out = _bank_n(tmp_path, 2), tmp_path / "batches"
    rkp.export_batches(bank, "r", out)
    (out / "batch_01.response.json").write_text(_raw_reviews(("q0", [])), encoding="utf-8")

    summary, errors = rkp.import_batches(bank, out)

    assert [r.question["id"] for r, _ in summary.reviewed] == ["q0"]
    assert any("q1: нет в ответе" in e for e in errors)


def _raw_reviews(*pairs):
    return json.dumps({"reviews": [{"id": i, "flags": f} for i, f in pairs]}, ensure_ascii=False)


def test_render_report_groups_by_severity(tmp_path):
    path = _bank(tmp_path)
    summary = rkp.run(path, FakeLlm(_raw(_flag(), _flag("1", "низкая"))), "r", dry_run=True)

    report = rkp.render_report(summary, "m", rkp.UsageMeter())

    assert "флагов: 2 (высокая 1, средняя 0, низкая 1)" in report
    assert report.index("## Серьёзность: высокая") < report.index("## Серьёзность: низкая")
    assert "| llm | Junior | 2. п2 | 1 |" in report
