from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .state import Answer, InterviewState

__all__ = ["Answer", "InterviewState", "build_graph"]


def __getattr__(name: str) -> Any:
    """Defer the heavy ``build_graph`` import until it's actually used.

    Importing ``sokrat`` for cheap things (like ``InterviewState`` or the
    validators) should not pull in ``langchain_openai`` and the rest of the
    LLM stack.
    """
    if name == "build_graph":
        from .graph import build_graph

        return build_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from .graph import build_graph as build_graph  # noqa: F401
