import json

import pytest

from sokrat import feedback, privacy, users
from sokrat.ratelimit import RateLimiter


def test_ratelimiter_blocks_after_limit_and_zero_disables():
    rl = RateLimiter()
    assert rl.hit("k", 2, 60) == 0
    assert rl.hit("k", 2, 60) == 0
    assert rl.hit("k", 2, 60) > 0
    assert rl.hit("other", 2, 60) == 0
    assert rl.hit("k", 0, 60) == 0


def test_feedback_replaces_same_session_and_summarizes(tmp_path):
    path = tmp_path / "fb.jsonl"
    feedback.save_feedback(session_id="s1", user="a", rating=2, path=path)
    feedback.save_feedback(session_id="s1", user="a", rating=4, realism=5, comment="ok", path=path)
    feedback.save_feedback(session_id="s2", user="b", rating=5, path=path)
    s = feedback.summary(path)
    assert s["count"] == 2 and s["avg_rating"] == 4.5 and s["avg_realism"] == 5
    assert feedback.delete_user_feedback("a", path=path) == 1
    assert feedback.summary(path)["count"] == 1


def test_delete_user_data_only_touches_owner(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "1.json").write_text(json.dumps({"user_id": "a"}))
    (sessions / "2.json").write_text(json.dumps({"user_id": "b"}))
    (sessions / "3.json").write_text(json.dumps({"summary": {}}))  # гостевая
    (sessions / "bad.json").write_text("{")
    res = privacy.delete_user_data("a", sessions_dir=sessions, live_sessions={})
    assert res["sessions"] == 1
    assert sorted(p.name for p in sessions.glob("*.json")) == ["2.json", "3.json", "bad.json"]
    assert privacy.delete_user_data("", sessions_dir=sessions)["sessions"] == 0


def test_users_delete(tmp_path):
    store = tmp_path / "users.json"
    assert users.register("alice", "secret1", path=store)
    assert users.delete("alice", path=store)
    assert not users.delete("alice", path=store)
    assert not users.verify("alice", "secret1", path=store)


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from sokrat import ratelimit
    from web import server

    ratelimit.limiter.reset()
    monkeypatch.setattr(feedback, "FEEDBACK_PATH", tmp_path / "fb.jsonl")
    monkeypatch.setattr(server, "INTERVIEWS_PER_DAY_GUEST", 2)
    return TestClient(server.app)


def test_guest_interview_quota(client):
    assert client.post("/api/interviews").status_code == 200
    assert client.post("/api/interviews").status_code == 200
    r = client.post("/api/interviews")
    assert r.status_code == 429 and "Лимит" in r.json()["error"]


def test_feedback_endpoint_validates(client):
    ok = client.post("/api/feedback", json={"session_id": "abcdef12-3456", "rating": 5, "realism": 4})
    assert ok.status_code == 200
    assert client.post("/api/feedback", json={"session_id": "abcdef12-3456", "rating": 9}).status_code == 422
    assert client.post("/api/feedback", json={"session_id": "../x", "rating": 3}).status_code == 400


def test_delete_data_requires_login(client):
    assert client.post("/api/account/delete-data", json={"password": "x"}).status_code == 401


def test_metrics_counts_and_logs_5xx(tmp_path):
    from sokrat.metrics import Metrics

    m = Metrics()
    err = tmp_path / "errors.jsonl"
    m.record("POST /a", 200, 0.5, errors_path=err)
    m.record("POST /a", 429, 0.1, errors_path=err)
    m.record("POST /a", 500, 1.5, errors_path=err)
    snap = m.snapshot()["routes"]["POST /a"]
    assert snap["count"] == 3 and snap["4xx"] == 1 and snap["5xx"] == 1 and snap["max_s"] == 1.5
    assert len(err.read_text().splitlines()) == 1


def test_active_and_admin_require_login(client):
    assert client.get("/api/interviews/active").status_code == 401
    assert client.get("/api/admin/stats").status_code == 401


def test_question_ratings_replace_and_rank(tmp_path):
    from sokrat import question_ratings as qr

    path = tmp_path / "qr.jsonl"
    qr.rate_question(session_id="s1", user="a", question="Что такое GIL?", value=1, path=path)
    qr.rate_question(session_id="s1", user="a", question="что  такое gil?", value=-1, path=path)  # замена
    qr.rate_question(session_id="s2", user="b", question="Что такое GIL?", value=-1, path=path)
    qr.rate_question(session_id="s2", user="b", question="Хороший вопрос", value=1, path=path)
    worst = qr.worst_questions(path=path)
    assert len(worst) == 1 and worst[0]["down"] == 2 and worst[0]["up"] == 0
    assert qr.delete_user_ratings("a", path=path) == 1


def test_admin_and_rating_endpoints(client):
    assert client.get("/api/admin/analytics").status_code == 401
    assert client.get("/admin").status_code == 200
    r = client.post("/api/question-rating", json={"session_id": "abcdef12-3456", "question": "Q?", "value": 0})
    assert r.status_code == 400


def test_funnel_and_events(tmp_path):
    from sokrat.metrics import Metrics, funnel

    ev = tmp_path / "events.jsonl"
    m = Metrics()
    for route in ("POST /api/interviews", "POST /api/interviews", "POST /api/interviews/{interview_id}/start",
                  "POST /api/interviews/{interview_id}/finish"):
        m.record(route, 200, 0.1, errors_path=tmp_path / "e.jsonl", events_path=ev)
    m.record("POST /api/interviews", 429, 0.1, errors_path=tmp_path / "e.jsonl", events_path=ev)  # не считается
    f = funnel(ev)
    assert f["created"] == 2 and f["started"] == 1 and f["finished"] == 1
    assert f["start_rate"] == 50.0 and f["finish_rate"] == 100.0


def test_pilot_stats(tmp_path):
    from sokrat.pilot_stats import pilot_stats

    def sess(name, user, ts, pct, vacancy=False):
        d = {"user_id": user, "finished_at": ts, "started_at": ts.replace("T10", "T09"),
             "summary": {"percentage": pct}, "scores": [{}] * 5}
        if vacancy:
            d["job_vacancy"] = {"company": "X"}
        (tmp_path / f"{name}.json").write_text(json.dumps(d))

    sess("1", "a", "2026-10-01T10:00:00", 40, True)
    sess("2", "a", "2026-10-02T10:00:00", 50)
    sess("3", "a", "2026-10-03T10:00:00", 70)
    sess("4", "b", "2026-10-01T10:00:00", 60)
    st = pilot_stats(tmp_path)
    assert st["sessions"] == 4 and st["vacancy_share"] == 25.0
    assert st["users"] == 2 and st["returning_share"] == 50.0
    assert st["avg_growth_pp"] == 30.0 and st["avg_minutes"] == 60.0


def test_cohorts_flow(tmp_path):
    from sokrat import cohorts

    store = tmp_path / "c.json"
    sessions = tmp_path / "s"
    sessions.mkdir()
    for i, (user, pct) in enumerate([("ann", 50), ("ann", 70), ("bob", 30), ("eve", 90)]):
        (sessions / f"{i}.json").write_text(json.dumps({
            "session_id": str(i), "user_id": user, "finished_at": f"2026-10-0{i + 1}T10:00:00",
            "summary": {"percentage": pct, "dimensions": {}}}))
    code = cohorts.create_cohort("ИТ-31", "admin", path=store)
    assert cohorts.join("ann", code.lower(), path=store) == "ИТ-31"
    assert cohorts.join("bob", code, path=store) == "ИТ-31"
    assert cohorts.join("bob", "NOPE12", path=store) is None
    assert cohorts.user_cohort("ann", path=store)["code"] == code
    st = cohorts.cohort_stats(code, path=store, sessions_dir=sessions)
    assert st["members"] == 2 and st["sessions"] == 3  # eve не в группе
    assert st["rows"][0]["user"] == "ann" and st["rows"][0]["trend"] == 20.0
    assert cohorts.leave("bob", path=store) and not cohorts.leave("bob", path=store)
    assert cohorts.cohort_stats("ZZZZZZ", path=store) is None
    with pytest.raises(ValueError):
        cohorts.create_cohort("  ", "admin", path=store)


def test_cohort_endpoints_closed_to_non_admins(client):
    assert client.get("/api/cohorts").status_code == 401
    assert client.post("/api/cohort/join", json={"code": "ABC"}).status_code == 401
