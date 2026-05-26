"""
Validator — structural (no LLM) + semantic (LLM, 3 stages).

Structural (no LLM):
  - Pydantic check on the OperationFragment.
  - Light OpenAPI checks: YAML serializable, $ref targets resolvable inside
    the merged document so far. Strict openapi-spec-validator runs only on
    the final assembled doc to avoid false negatives on partial fragments.

Semantic (LLM, 3 ordered stages — same shape as openapi_rulesbank):
  Stage 1 — Op init: reconcile previous-iteration validator_op_reflection.
  Stage 2 — Per-item verdict (valid / correction / split / discard) + new_missing_rules.
  Stage 3 — Reflector review: incorporate fragment_reflection into the final
            validator_op_reflection.

Counts only 'correction' errors against MAX_OP_ITERATIONS for retry routing.
'split' and 'discard' are routed via validator_op_reflection on the next pass.

Inputs (state): reflected_fragment, fragment_reflection,
                validator_op_reflection (previous), validated_fragments_by_op
Outputs (state): validation_errors, validator_op_reflection,
                 validated_fragments_by_op (compact summary)
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def validator_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
    logger.info("openapi_generator.validator → STUB (not yet implemented)")
    return {
        "validation_errors": [],
        "validator_op_reflection": {"missing_rules": []},
        "validated_fragments_by_op": state.get("validated_fragments_by_op", {}),
    }
