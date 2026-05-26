"""
Pydantic models exchanged between nodes.

Field descriptions are deliberately verbose because they double as the schema
hint sent to the LLM via `llm.with_structured_output(...)`.
"""

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

Action = Literal["create", "update", "keep"]
Priority = Literal["high", "medium", "low"]


class TargetOperation(BaseModel):
    """One unit of work for the per-operation loop.

    Identifies a single OpenAPI operation (path + HTTP method) that the
    Patcher will generate, modify, or leave alone — together with which
    rules from rules_bank justify it.
    """

    path: str = Field(
        description=(
            "OpenAPI path template, exactly as it should appear under "
            "`paths` in the final document (e.g. '/{className}={id}', "
            "'/subscriptions', '/subscriptions/{subscriptionId}'). Preserve "
            "braces around path variables."
        )
    )
    method: Literal["get", "put", "post", "delete", "patch", "head", "options"] = Field(
        description="Lowercase HTTP method for this operation."
    )
    action: Action = Field(
        description=(
            "What the Patcher must do with this (path, method):\n"
            "  'create' — operation absent from the legacy OpenAPI; build "
            "from scratch using the rules and the 3GPP spec.\n"
            "  'update' — operation exists in the legacy but rules require "
            "changes (new parameters, schema fields, responses, etc.).\n"
            "  'keep'   — operation exists in the legacy and the rules "
            "confirm it as-is; no LLM work needed, just carry it forward."
        )
    )
    source_rule_ids: List[int] = Field(
        default_factory=list,
        description=(
            "Zero-based indices into rules_bank['rules'] for every rule "
            "that grounds this operation (path_operation, path_parameter, "
            "query_parameter, request_body, response, schema_property, "
            "security_scheme). Empty list is only acceptable for 'keep'."
        ),
    )
    priority: Priority = Field(
        default="medium",
        description=(
            "Execution priority. 'high' for operations whose schemas are "
            "referenced by others (process first so $refs resolve), "
            "'medium' for standard CRUD, 'low' for auxiliary endpoints."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "One short sentence explaining why this operation was selected "
            "and the chosen action — useful for debugging the plan."
        ),
    )


class ExtractionPlan(BaseModel):
    """Planner output — the ordered list of operations the loop will process."""

    document_summary: str = Field(
        default="",
        description=(
            "One or two sentences summarizing what API surface this plan "
            "covers (e.g. 'CRUD for Managed Object Instances over the "
            "Provisioning MnS interface')."
        ),
    )
    operations_to_generate: List[TargetOperation] = Field(
        default_factory=list,
        description=(
            "Ordered list of every (path, method) the Patcher must process. "
            "Order matters: schemas referenced by other operations should "
            "come first so that $refs resolve incrementally. Do not include "
            "duplicates. Cover all rules in rules_bank — every rule index "
            "should appear in at least one operation's source_rule_ids."
        ),
    )


class OperationFragment(BaseModel):
    """
    A self-contained OpenAPI fragment produced by the Patcher for one operation.
    Will be merged into the final document by the Assembler.
    """

    path: str
    method: str
    paths: Dict[str, Any] = Field(default_factory=dict)
    components: Dict[str, Any] = Field(default_factory=dict)


class ValidationVerdict(BaseModel):
    """Per-fragment verdict produced by the Validator (semantic stage 2)."""

    verdict: Literal["valid", "correction", "split", "discard"]
    instruction: str = ""
    new_missing_rules: List[str] = Field(default_factory=list)
