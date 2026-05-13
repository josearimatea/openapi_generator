"""
LangGraph definition for the OpenAPI generation pipeline.

Per-operation loop with two conditional edges (mirrors openapi_rulesbank):

    [loader] → [planner] → [patcher] → [reflector] → [validator] ──→ [assembler] → END
                              ↑                          │                │
                              │   error rate > threshold │   next op      │
                              │   AND retries < max      │                │
                              └────── retry op ──────────┘                │
                              └─────────────── next op ──────────────────┘

The checkpointer is injected by the caller (e.g. a SqliteSaver from the
chatbot service) so this package stays free of filesystem side effects at
import time.
"""

import logging

from langgraph.graph import END, StateGraph

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

logger = logging.getLogger(__name__)


def build_openapi_gen_graph(checkpointer=None):
    """Build and compile the OpenAPI generation graph.

    Args:
        checkpointer: Optional LangGraph checkpointer (e.g. SqliteSaver,
            MemorySaver). If None, the graph is compiled without persistence.
    """
    graph = StateGraph(OpenAPIGenState)

    graph.add_node("loader", loader_node)
    graph.add_node("planner", planner_node)
    graph.add_node("patcher", patcher_node)
    graph.add_node("reflector", reflector_node)
    graph.add_node("validator", validator_node)
    graph.add_node("assembler", assembler_node)

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
