"""
Patcher — LLM (1 call/op) + RAG.

For the current target operation:
1. Pull the relevant fragment from the legacy OpenAPI (RAG-spec or direct lookup
   when the path exists).
2. Pull supporting 3GPP context from the chat RAG when the rules_bank is
   insufficient.
3. Apply the rules from rules_bank to emit an OperationFragment
   (paths.<path>.<method> + any new components.schemas referenced).

On retry, also reads validation_errors (filtered to 'correction' only) and
validator_op_reflection from the previous Validator pass.

Inputs (state): operations_plan, current_op_idx, rules_bank, legacy_spec,
                validation_errors, validator_op_reflection
Outputs (state): current_fragment, op_iteration_count (+1)
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def patcher_node(state: dict) -> Dict[str, Any]:
    logger.info("openapi_generator.patcher → STUB (not yet implemented)")
    iteration = state.get("op_iteration_count", 0) + 1
    return {
        "current_fragment": {"path": "", "method": "", "paths": {}, "components": {}},
        "op_iteration_count": iteration,
    }
