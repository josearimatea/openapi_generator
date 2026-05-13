"""
Assembler — deterministic, no LLM.

Called once per operation. Merges the validated fragment into the accumulated
final OpenAPI document, then advances the loop:

1. Force-include at max retries: if op_iteration_count >= MAX_OP_ITERATIONS and
   correction errors remain, accept the current fragment with a flag/note so
   nothing is lost.
2. Merge:
     final_openapi.paths       ← deep-merge with current_fragment.paths
     final_openapi.components  ← deep-merge with current_fragment.components
3. Save a snapshot to disk (per-op or final).
4. Advance: current_op_idx += 1, op_iteration_count = 0,
            clear current_fragment / validation_errors / fragment_reflection /
            validator_op_reflection.
5. On the last operation, write the final YAML to openapi_target_path.

Inputs (state): reflected_fragment, validation_errors, op_iteration_count,
                final_openapi, openapi_target_path, current_op_idx, operations_plan
Outputs (state): final_openapi, final_output_path, current_op_idx (+1),
                 op_iteration_count (=0), cleared per-op fields.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def assembler_node(state: dict) -> Dict[str, Any]:
    logger.info("openapi_generator.assembler → STUB (not yet implemented)")
    next_idx = state.get("current_op_idx", 0) + 1
    return {
        "final_openapi": state.get("final_openapi", {}),
        "final_output_path": state.get("openapi_target_path", ""),
        "current_op_idx": next_idx,
        "op_iteration_count": 0,
        "current_fragment": {},
        "validation_errors": [],
        "fragment_reflection": {},
        "validator_op_reflection": {},
    }
