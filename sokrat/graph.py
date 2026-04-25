from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .nodes.analyzer import analyze_answer_node
from .nodes.collect_context import collect_context_node
from .nodes.greeting import greeting_node
from .nodes.interview import interview_node
from .nodes.summary import summary_node
from .state import InterviewState


def _route_after_collect_context(state: InterviewState) -> str:
    if state.get("current_step") == "interview":
        return "interview"
    return "collect_context"


def _route_after_analyze(state: InterviewState) -> str:
    return state.get("current_step", "summary")


def build_graph():
    g: StateGraph = StateGraph(InterviewState)
    g.add_node("greeting", greeting_node)
    g.add_node("collect_context", collect_context_node)
    g.add_node("interview", interview_node)
    g.add_node("analyze_answer", analyze_answer_node)
    g.add_node("summary", summary_node)

    g.set_entry_point("greeting")
    g.add_edge("greeting", "collect_context")

    g.add_conditional_edges(
        "collect_context",
        _route_after_collect_context,
        {"interview": "interview", "collect_context": "collect_context"},
    )

    g.add_edge("interview", "analyze_answer")

    g.add_conditional_edges(
        "analyze_answer",
        _route_after_analyze,
        {"interview": "interview", "summary": "summary"},
    )

    g.add_edge("summary", END)

    return g.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["collect_context", "analyze_answer"],
    )
