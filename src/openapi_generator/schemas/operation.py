"""
Pydantic models exchanged between nodes.

Kept minimal on purpose — fill in stricter validation as each node is implemented.
"""

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


Action = Literal["create", "update", "keep"]
Priority = Literal["high", "medium", "low"]


class TargetOperation(BaseModel):
    """One unit of work for the per-operation loop."""

    path: str
    method: str  # "get" | "put" | "post" | "delete" | "patch"
    action: Action
    source_rule_ids: List[int] = Field(default_factory=list)
    priority: Priority = "medium"


class ExtractionPlan(BaseModel):
    """Planner output."""

    document_summary: str = ""
    operations_to_generate: List[TargetOperation] = Field(default_factory=list)


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
