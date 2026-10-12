from __future__ import annotations

from typing import Any, Literal, TypedDict

from typing_extensions import NotRequired

Level = Literal["Junior", "Middle", "Senior"]
InterviewType = Literal["technical", "hr", "mixed"]
StepName = Literal[
    "greeting",
    "collect_context",
    "interview",
    "analyze_answer",
    "summary",
    "save_session",
    "done",
]


class Answer(TypedDict):
    question: str
    answer: str
    score: int
    strengths: list[str]
    weaknesses: list[str]
    feedback: str
    category: str


class InterviewState(TypedDict, total=False):
    """Single source of truth for an interview session.

    All fields except `session_id` and `current_step` are optional — the graph
    populates them as the dialog progresses. App.py mirrors this state to
    `cl.user_session` and back when invoking the graph.
    """
    session_id: str
    started_at: NotRequired[str]
    finished_at: NotRequired[str]

    role: NotRequired[str]
    role_category: NotRequired[str]
    level: NotRequired[str]
    interview_type: NotRequired[str]
    interview_mode: NotRequired[str]
    required_skills: NotRequired[list[str]]
    is_custom_role: NotRequired[bool]
    job_vacancy: NotRequired[dict[str, Any]]
    company_profile: NotRequired[dict[str, Any]]
    resume_profile: NotRequired[dict[str, Any]]
    gap_analysis: NotRequired[dict[str, Any]]

    # Pre-built question pool + cursor.
    questions: NotRequired[list[dict[str, Any]]]
    question_num: NotRequired[int]
    current_question: NotRequired[dict[str, Any]]

    # Per-turn input/output.
    user_input: NotRequired[str]
    assistant_output: NotRequired[str]
    elapsed_seconds: NotRequired[float]
    clarifications_used: NotRequired[int]
    last_analysis: NotRequired[dict[str, Any]]

    # Accumulated history.
    answers: NotRequired[list[str]]
    scores: NotRequired[list[dict[str, Any]]]

    # Final report.
    summary: NotRequired[dict[str, Any]]
    saved_path: NotRequired[str]

    current_step: StepName
    errors: NotRequired[list[str]]
