"""
Pydantic models exchanged between nodes.

Field descriptions are deliberately verbose because they double as the schema
hint sent to the LLM via `llm.with_structured_output(...)`.
"""

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

Action = Literal["create", "update", "keep", "discard"]
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
            "What the Patcher (or Assembler) must do with this (path, method):\n"
            "  'create'  — operation absent from the legacy; build from scratch.\n"
            "  'update'  — operation exists in the legacy but rules/spec require "
            "changes.\n"
            "  'keep'    — operation exists in the legacy and rules/spec confirm "
            "it as-is.\n"
            "  'discard' — operation exists in the legacy but rules/spec show "
            "it must be removed (e.g. deprecated in Rel-18). Assembler drops "
            "it from the final document; Patcher is not called."
        )
    )
    source_rule_ids: List[int] = Field(
        default_factory=list,
        description=(
            "Zero-based indices into rules_bank['rules'] for every rule "
            "that grounds this operation, of any type in "
            "schemas.rule_types.RULE_TYPES. Empty list is only acceptable "
            "for 'keep'."
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
    probable_gaps: List[str] = Field(
        default_factory=list,
        description=(
            "Debug aid: spec section titles (or rule descriptions) that the "
            "Planner could not associate with any TargetOperation. Useful to "
            "spot 3GPP requirements that may have been dropped."
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


class PlannerPass1Verdict(BaseModel):
    """
    Verdict returned by the Planner Pass 1 (per legacy operation).

    The Planner looks at a single legacy (path, method), the candidate rules
    pre-filtered for it, plus 3GPP and OpenAPI-reference RAG chunks, and
    decides what to do with it in the final document.
    """

    action: Action = Field(
        description=(
            "'keep' if the legacy fragment already satisfies every relevant "
            "rule and the spec confirms it; 'update' if rules/spec require "
            "changes on top of the legacy; 'discard' if rules/spec indicate "
            "the operation must be removed (deprecated, replaced)."
        )
    )
    source_rule_ids: List[int] = Field(
        default_factory=list,
        description=(
            "Subset of the candidate rule indices that DO apply to this "
            "operation. Indices from the candidate table only; never invent."
        ),
    )
    rationale: str = Field(
        default="",
        description="One short sentence justifying the chosen action.",
    )


class PlannerPass2Op(BaseModel):
    """
    Pass 2 verdict for ONE group of residual rules.

    Two destinations are allowed:
      - 'new'    : the rules describe an operation that did NOT exist in the
                   legacy. The Planner emits a new TargetOperation with
                   action='create' using path/method/priority below.
      - 'attach' : the rules actually belong to an operation already in the
                   plan (typically a Pass-1 entry from the legacy). The
                   Planner appends `source_rule_ids` to that existing
                   operation; `attach_to_index` points at it.
    """

    destination: Literal["new", "attach"] = Field(
        description=(
            "'new' → emit a new TargetOperation with action='create'. "
            "'attach' → append source_rule_ids to operations_plan[attach_to_index]."
        )
    )
    path: str = Field(
        default="",
        description="Required when destination='new'; ignored otherwise.",
    )
    method: Literal["get", "put", "post", "delete", "patch", "head", "options", ""] = Field(
        default="",
        description="Required when destination='new'; ignored otherwise.",
    )
    attach_to_index: int = Field(
        default=-1,
        description=(
            "Required when destination='attach'. Zero-based index into the "
            "operations_plan list shown to the LLM."
        ),
    )
    source_rule_ids: List[int] = Field(
        default_factory=list,
        description=(
            "Subset of the candidate rule indices that truly apply to the "
            "chosen destination."
        ),
    )
    priority: Priority = Field(default="medium")
    rationale: str = Field(default="")


class PlannerSchemaAttachment(BaseModel):
    """
    Pass 2 Phase C verdict: which operations in the current plan should
    receive the rule indices that describe properties of one schema.

    The LLM sees the schema name, its grouped schema_property rules, and
    the current plan. It returns the indices of operations whose request
    body / response / parameters use that schema.
    """

    attach_to_indices: List[int] = Field(
        default_factory=list,
        description=(
            "Zero-based indices into operations_plan whose operations use "
            "this schema (e.g. via $ref in requestBody or responses). "
            "Empty list means no operation in the plan uses the schema — "
            "Planner records every rule index as a probable gap."
        ),
    )
    rationale: str = Field(default="")


class DocumentMetadata(BaseModel):
    """What a specification's cover page says about the document itself.

    Read by an LLM rather than parsed: the cover survives conversion from PDF
    or DOCX in whatever shape the converter produces — a Markdown table, a run
    of loose lines — so the values are stated plainly but never in a
    predictable position. Every field is optional; report only what the page
    actually says, and leave the rest empty rather than inventing it.
    """

    number: str = Field(
        default="",
        description="Specification number as printed, e.g. '28.532'. Digits and dot only.",
    )
    version: str = Field(
        default="",
        description="Document version as printed, e.g. '18.0.0'. No leading 'V'.",
    )
    release: str = Field(
        default="", description="Release number alone, e.g. '18'."
    )
    subject: str = Field(
        default="",
        description=(
            "What this document is about, in its own words — the title line "
            "naming the subject, not the organisation or the working group."
        ),
    )
    copyright: str = Field(
        default="",
        description=(
            "The copyright notice as one line, from the © through the rights "
            "reservation, exactly as worded."
        ),
    )


class ServerVariable(BaseModel):
    """One variable of a Server Object's URL template."""

    name: str = Field(description="Variable name, matching a {placeholder} in the url.")
    default: str = Field(
        default="",
        description=(
            "Value the specification states for this variable. Empty when the "
            "normative text names the variable without giving a value — do not "
            "invent one."
        ),
    )
    description: str = Field(
        default="",
        description="How the specification defines the variable, in its own words.",
    )


class ServerDecision(BaseModel):
    """Where the service described by this document is hosted.

    Produced by the servers pass, which reads the specification's URI clauses
    and RAG context — the generator owns this block rather than taking it from
    the rules bank. `url` empty means the specification declares no server for
    this service — a notification sink addressed through a subscription, say —
    and the caller falls back to a marked placeholder rather than a plausible
    guess.
    """

    url: str = Field(
        default="",
        description=(
            "Server URL template, e.g. '{Root}/SomeService/{Version}'. Include "
            "only the part that locates the service; anything identifying a "
            "resource belongs to paths, not here. Empty when the specification "
            "states no server for this document."
        ),
    )
    variables: List[ServerVariable] = Field(
        default_factory=list,
        description="One entry per {placeholder} appearing in url.",
    )
    description: str = Field(
        default="",
        description="Short note on where the service is hosted, when the spec says.",
    )
    rationale: str = Field(
        default="",
        description="Which passage settled it, or why the spec states no server.",
    )


class PlannerUngroupedMapping(BaseModel):
    """
    Pass 2 epilogue: bulk-mapping for rules whose openapi_object did not
    expose a (path, method) so the deterministic grouping could not place
    them. One LLM call receives all ungrouped rules + the current plan and
    decides, per rule, the best attachment.
    """

    class Entry(BaseModel):
        rule_index: int = Field(description="Ungrouped rule index from the input list.")
        attach_to_index: int = Field(
            default=-1,
            description=(
                "Operation index in the plan to attach this rule to. Use -1 "
                "when no operation is a plausible fit (Planner records it as "
                "a probable gap)."
            ),
        )

    assignments: List[Entry] = Field(default_factory=list)
