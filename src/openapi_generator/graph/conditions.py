"""
Conditional edges for the openapi_generator graph.

Two routing decisions:
- After Validator: retry current operation (back to Patcher) or proceed to Assembler.
- After Assembler: process next operation (back to Patcher) or end.
"""

import logging

logger = logging.getLogger(__name__)

# These will become real settings once the pipeline is wired.
# Kept here as module-level constants for now; promote to a settings module later.
MAX_OP_ITERATIONS = 3
VALIDATION_ERROR_THRESHOLD = 0.10  # 10% of fragment items flagged as 'correction'


def should_retry_op_or_assemble(state: dict) -> str:
    """Decide whether to retry the current operation or move to the Assembler."""
    errors = state.get("validation_errors") or []
    correction_errors = [e for e in errors if e.get("error_type") == "correction"]
    iteration = state.get("op_iteration_count", 0)

    # Without fragment items the rate is undefined — treat as zero errors.
    fragment = state.get("reflected_fragment") or {}
    items = fragment.get("items") or fragment or [None]
    error_rate = len(correction_errors) / max(len(items), 1)

    if error_rate > VALIDATION_ERROR_THRESHOLD and iteration < MAX_OP_ITERATIONS:
        logger.info(f"Routing → retry op (rate={error_rate:.2f}, iter={iteration})")
        return "retry"
    logger.info(f"Routing → assemble (rate={error_rate:.2f}, iter={iteration})")
    return "assemble"


def should_next_op_or_end(state: dict) -> str:
    """Decide whether more operations remain to be processed."""
    plan = state.get("operations_plan") or []
    idx = state.get("current_op_idx", 0)
    if idx < len(plan):
        logger.info(f"Routing → next op ({idx}/{len(plan)})")
        return "next"
    logger.info("Routing → end (all ops processed)")
    return "__end__"
