"""
Conditional edges for the openapi_generator graph.

Two routing decisions:
- After the Assembler: build the next operation, or move on to the review once
  every operation is in the document.
- After the Validator: hand its fixes back to the Patcher, or finish.
"""

import logging

logger = logging.getLogger(__name__)

# How many times the document may go back for corrections. Each round costs a
# Reflector pair and a Validator call, and a document still wrong after three
# is unlikely to be fixed by a fourth.
MAX_DOCUMENT_ITERATIONS = 3


def should_retry_op_or_assemble(state: dict) -> str:
    """Send the Validator's fixes back to the Patcher, or finish.

    The Validator returns fixes only for problems the Reflector confirmed, so
    any fix at all is worth a round — there is no error rate to weigh. What
    stops the loop is the budget: without it a document the Patcher cannot
    satisfy would cycle indefinitely.
    """
    fixes = state.get("validation_errors") or []
    iteration = state.get("op_iteration_count", 0)

    if not fixes:
        logger.info("Routing → done (nothing left to fix)")
        return "assemble"

    if iteration >= MAX_DOCUMENT_ITERATIONS:
        logger.warning(
            f"Routing → done with {len(fixes)} fix(es) outstanding "
            f"(reached {MAX_DOCUMENT_ITERATIONS} correction rounds)"
        )
        return "assemble"

    logger.info(f"Routing → correcting {len(fixes)} fix(es), round {iteration + 1}")
    return "retry"


def should_assemble_or_review(state: dict) -> str:
    """Merge what the Patcher just built, or send its corrections back for review.

    The Patcher has two modes. Building an operation leaves a fragment for the
    Assembler to merge; correcting the assembled document leaves nothing to
    merge — it edited the document in place — so that path returns straight to
    the review that asked for the changes.
    """
    if state.get("current_fragment"):
        return "assemble"
    logger.info("Routing → review (the document was corrected in place)")
    return "review"


def should_next_op_or_end(state: dict) -> str:
    """Build the next operation, or send the finished document to the review."""
    plan = state.get("operations_plan") or []
    idx = state.get("current_op_idx", 0)
    if idx < len(plan):
        logger.info(f"Routing → next operation ({idx}/{len(plan)})")
        return "next"
    logger.info("Routing → review (every operation is in the document)")
    return "__end__"
