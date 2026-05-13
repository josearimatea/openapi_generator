"""
Planner — LLM (1 call).

Reads the rules_bank (grouped by openapi_object/path) and the legacy OpenAPI
to produce an ordered list of TargetOperation items: which (path, method)
to generate, whether to create/update/keep, and which rule IDs ground each.

Inputs (state): rules_bank, legacy_spec
Outputs (state): operations_plan
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def planner_node(state: dict) -> Dict[str, Any]:
    logger.info("openapi_generator.planner → STUB (not yet implemented)")
    return {"operations_plan": []}
