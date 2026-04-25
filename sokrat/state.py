from __future__ import annotations

from typing import Literal, TypedDict

from typing_extensions import NotRequired

Level = Literal["junior", "middle", "senior"]
InterviewType = Literal["technical", "hr", "mixed"]
StepName = Literal[
    "greeting",
    "collect_context",
    "interview",
    "analyze_answer",
    "summary",
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


class InterviewState(TypedDict):
    session_id: str
    started_at: str
    finished_at: NotRequired[str]

    role: NotRequired[str]
    level: NotRequired[Level]
    interview_type: NotRequired[InterviewType]

    user_input: NotRequired[str]
    assistant_output: NotRequired[str]

    questions_asked: list[str]
    answers: list[Answer]
    current_question: NotRequired[str]
    current_step: StepName
    questions_total: int

    summary: NotRequired[str]
    overall_score: NotRequired[float]

    errors: list[str]
