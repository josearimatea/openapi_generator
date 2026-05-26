"""
LangGraph definition for the OpenAPI generation pipeline.

Per-operation loop with two conditional edges (mirrors openapi_rulesbank):

    [loader] → [planner] → [patcher] → [reflector] → [validator] ──→ [assembler] → END
                              ↑                          │                │
                              │   error rate > threshold │   next op      │
                              │   AND retries < max      │                │
                              └────── retry op ──────────┘                │
                              └─────────────── next op ──────────────────┘

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
    graph.add_edge("patcher", "reflector")
    graph.add_edge("reflector", "validator")

    # Validator → retry patcher OR proceed to assembler
    graph.add_conditional_edges(
        "validator",
        should_retry_op_or_assemble,
        {
            "retry": "patcher",
            "assemble": "assembler",
        },
    )

    # Assembler → next operation (back to patcher) OR end
    graph.add_conditional_edges(
        "assembler",
        should_next_op_or_end,
        {
            "next": "patcher",
            "__end__": END,
        },
    )

    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()
