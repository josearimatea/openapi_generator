"""
LangGraph definition for the OpenAPI generation pipeline.

Two loops: one builds the document an operation at a time, the other reviews
and corrects the finished whole.

    [loader] → [planner] → [patcher] → [assembler] ─── next op ───┐
                              ↑                │                  │
                              └────────────────┴──────────────────┘
                                               │ every operation merged
                                               ▼
                              ┌───────── [reflector] → [validator] → END
                              │              ▲              │
                              └── corrections┘              │ fixes to apply
                                  applied in place ─────────┘

The Assembler follows the Patcher directly, merging and writing the document
after each operation — as openapi_rulesbank's Builder saves its bank after each
section — so a failure partway costs the operations still to come rather than
the ones already done.

Review runs on the assembled document rather than on a fragment: the Reflector
checks it structurally, reads it part by part, then weighs everything against
the whole; the Validator turns what it confirmed into fixes; and the Patcher
applies them in place, leaving untouched whatever no fix names.

Dependency injection (all optional):
  - checkpointer: LangGraph saver chosen by the host application (SqliteSaver,
    MemorySaver, ...). None → graph compiled without persistence.
  - llm: a LangChain-compatible chat model. None → nodes fall back to
    openapi_generator.config.llm_config.get_llm() (default ChatOpenAI).
  - retriever: a callable(query, k, filters) → list[str] matching
    openapi_generator.rag.retriever.Retriever. None → nodes fall back to
    get_relevant_chunks (which itself degrades to [] if Qdrant/collection
    is unavailable).

Every node accepts (state, llm=None, retriever=None) so the build function
can inject the same dependency pair uniformly. Nodes that don't need a
dependency simply ignore the kwarg.
"""

from functools import partial
from typing import Any, Callable, Optional

from langgraph.graph import END, StateGraph

from openapi_generator.config import get_logger
from openapi_generator.graph.conditions import (
    should_assemble_or_review,
    should_next_op_or_end,
    should_retry_op_or_assemble,
)
from openapi_generator.graph.state import OpenAPIGenState
from openapi_generator.nodes.assembler import assembler_node
from openapi_generator.nodes.loader import loader_node
from openapi_generator.nodes.patcher import patcher_node
from openapi_generator.nodes.planner import planner_node
from openapi_generator.nodes.reflector import reflector_node
from openapi_generator.nodes.validator import validator_node

logger = get_logger(__name__)


def build_openapi_gen_graph(
    checkpointer: Optional[Any] = None,
    llm: Optional[Any] = None,
    retriever: Optional[Callable] = None,
):
    """Build and compile the OpenAPI generation graph.

    Args:
        checkpointer: Optional LangGraph checkpointer. None → no persistence.
        llm: Optional LangChain chat model. None → default ChatOpenAI from
            settings (lazy).
        retriever: Optional callable(query, k, filters) → list[str]. None →
            default Qdrant-backed retriever that degrades to [] if unavailable.
    """
    graph = StateGraph(OpenAPIGenState)

    graph.add_node("loader",    partial(loader_node,    llm=llm, retriever=retriever))
    graph.add_node("planner",   partial(planner_node,   llm=llm, retriever=retriever))
    graph.add_node("patcher",   partial(patcher_node,   llm=llm, retriever=retriever))
    graph.add_node("reflector", partial(reflector_node, llm=llm, retriever=retriever))
    graph.add_node("validator", partial(validator_node, llm=llm, retriever=retriever))
    graph.add_node("assembler", partial(assembler_node, llm=llm, retriever=retriever))

    graph.set_entry_point("loader")

    # Linear setup phase
    graph.add_edge("loader", "planner")
    graph.add_edge("planner", "patcher")

    # While operations remain, the Assembler follows the Patcher directly,
    # merging and saving after each one, so the document grows on disk as it is
    # built and a failure partway does not cost the work already done. Once the
    # Patcher is correcting the assembled document instead of adding to it,
    # there is nothing to merge and it goes straight back for review.
    graph.add_conditional_edges(
        "patcher",
        should_assemble_or_review,
        {
            "assemble": "assembler",
            "review": "reflector",
        },
    )

    # Assembler → next operation, or on to the review once every operation is in
    graph.add_conditional_edges(
        "assembler",
        should_next_op_or_end,
        {
            "next": "patcher",
            "__end__": "reflector",
        },
    )

    # Review runs on the assembled document, not on a fragment: the Reflector
    # reports what is wrong, the Validator says what to do about it.
    graph.add_edge("reflector", "validator")

    # Validator → hand the fixes to the Patcher, or finish
    graph.add_conditional_edges(
        "validator",
        should_retry_op_or_assemble,
        {
            "retry": "patcher",
            "assemble": END,
        },
    )

    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()
