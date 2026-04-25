from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes.analyzer import analyze_answer_node
from .nodes.collect_context import collect_context_node
from .nodes.greeting import greeting_node
from .nodes.interview import interview_node
from .nodes.save_session import save_session_node
from .nodes.summary import summary_node
from .state import InterviewState

# Phases the UI layer requests via state["current_step"]. Each enters at a
# different node so a single compiled graph handles every dialog transition.
PHASE_START = "start"                  # session init: greeting → collect_context
PHASE_NEXT_QUESTION = "next_question"  # advance to the next question
PHASE_ANALYZE = "analyze"              # score one user answer
PHASE_FINALIZE = "finalize"            # build summary
PHASE_PERSIST = "persist"              # write session to disk


def _route_from_start(state: InterviewState) -> str:
    step = state.get("current_step", PHASE_START)
    if step == PHASE_NEXT_QUESTION:
        return "interview"
    if step == PHASE_ANALYZE:
        return "analyze_answer"
    if step == PHASE_FINALIZE:
        return "summarize"
    if step == PHASE_PERSIST:
        return "save_session"
    return "greeting"


def build_graph():
    g: StateGraph = StateGraph(InterviewState)
    g.add_node("greeting", greeting_node)
    g.add_node("collect_context", collect_context_node)
    g.add_node("interview", interview_node)
    g.add_node("analyze_answer", analyze_answer_node)
    g.add_node("summarize", summary_node)
    g.add_node("save_session", save_session_node)

    g.add_conditional_edges(
        START,
        _route_from_start,
        {
            "greeting": "greeting",
            "interview": "interview",
            "analyze_answer": "analyze_answer",
            "summarize": "summarize",
            "save_session": "save_session",
        },
    )

    # Init flow.
    g.add_edge("greeting", "collect_context")
    g.add_edge("collect_context", END)

    # Per-question slices.
    g.add_edge("interview", END)
    g.add_edge("analyze_answer", END)

    # Finalize is split into two phases so the UI layer can inject extras
    # (e.g. weak-topic resources) into state.summary between them.
    g.add_edge("summarize", END)
    g.add_edge("save_session", END)

    return g.compile()
