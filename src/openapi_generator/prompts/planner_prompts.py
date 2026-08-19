"""
Prompt templates for the Planner node.

The Planner runs in TWO passes:

  Pass 1 — Polish the legacy
    For each (path, method) already present in legacy_openapi, ask the LLM
    to decide keep / update / discard based on:
      - the legacy fragment itself
      - the candidate rules pre-filtered deterministically from rules_bank
      - 3GPP spec chunks retrieved via RAG
      - OpenAPI 3.0 reference chunks retrieved via RAG
    The LLM returns a PlannerPass1Verdict with the chosen action and the
    rule indices that actually apply.

  Pass 2 — Create new operations from leftover rules
    Rules not consumed by Pass 1 are grouped deterministically by their
    inferred (path, method) (from openapi_mapping.openapi_object). For each
    group, the LLM confirms / refines a new TargetOperation with action='create'
    using the same RAG support.

Inputs interpolated:
  Pass 1:
    op_label            — "GET /{className}={id}"
    legacy_fragment     — pretty-printed YAML fragment for the op
    candidate_rules     — rules whose openapi_object touches the same path/schema
    spec_rag            — 3GPP chunks (similarity_search)
    openapi_reference   — OpenAPI 3.0 chunks (similarity_search)

  Pass 2:
    op_label            — "PUT /subscriptions"
    grouped_rules       — residual rules sharing this (path, method)
    spec_rag            — 3GPP chunks
    openapi_reference   — OpenAPI 3.0 chunks
"""

from langchain_core.prompts import ChatPromptTemplate


# ── PASS 1 ───────────────────────────────────────────────────────────────────

_PASS1_SYSTEM = """\
You are an expert in 3GPP technical specifications and OpenAPI 3.0 design.

You are reviewing ONE operation that already exists in a LEGACY OpenAPI
document. Your job is to decide what happens to this operation in the new
document the pipeline is building.

Inputs you receive (in priority order):
  1. LEGACY FRAGMENT  — the starting point. Treat as authoritative unless the
                        rules or the spec contradict it.
  2. CANDIDATE RULES  — rules from rules_bank that were deterministically
                        pre-filtered to touch this (path, method). Each rule
                        has an INDEX you must reference. Not every candidate
                        truly applies; you decide which do.
  3. 3GPP SPEC CHUNKS — supporting passages retrieved via RAG to confirm
                        whether the legacy is still aligned with the spec.
  4. OPENAPI REFERENCE — authoritative OpenAPI 3.0 excerpts for syntax /
                         keyword validation.

Decide ONE action:
  • 'keep'    — legacy is fine as-is; every relevant rule is already satisfied
                and nothing in the spec demands changes.
  • 'update'  — legacy is mostly correct but at least one rule or spec passage
                requires changes (new param, new response, schema tweak…).
  • 'discard' — legacy operation must be removed (deprecated, replaced by a
                different operation, contradicted by the spec).

Also list `source_rule_ids` — the subset of candidate rule indices that
ACTUALLY apply. Empty list is fine when 'keep' and no rule is needed.

NEVER invent rule indices. NEVER change path or method. Only the action
and rule association are yours to decide.
"""

_PASS1_USER = """\
OPERATION UNDER REVIEW: {op_label}

LEGACY FRAGMENT (authoritative baseline):
{legacy_fragment}

CANDIDATE RULES (already filtered for this operation; pick the ones that apply):
{candidate_rules}

3GPP SPEC CHUNKS (use to confirm or contradict the legacy):
{spec_rag}

OPENAPI 3.0 REFERENCE CHUNKS (syntax / keyword authority):
{openapi_reference}

Produce the PlannerPass1Verdict now. Remember:
  - source_rule_ids must be a subset of the candidate indices shown above.
  - Choose 'discard' only when the legacy operation should NOT appear in the
    final document at all.
"""

pass1_prompt = ChatPromptTemplate.from_messages([
    ("system", _PASS1_SYSTEM),
    ("human", _PASS1_USER),
])


# ── PASS 2 — per group ───────────────────────────────────────────────────────

_PASS2_SYSTEM = """\
You are an expert in 3GPP technical specifications and OpenAPI 3.0 design.

A deterministic step already grouped residual rules (rules NOT consumed by
Pass 1) by their inferred (path, method). You receive ONE such group and
decide where its rules belong in the final plan.

Two destinations are possible:

  • 'new'    — the rules describe an operation that does NOT yet exist
               in the plan. Emit a NEW TargetOperation (action='create')
               with the candidate path/method, refining source_rule_ids
               to the ones that genuinely apply.

  • 'attach' — the rules actually belong to an operation ALREADY in the
               plan (typically a Pass-1 legacy operation that the
               deterministic filter missed). Choose its index in the
               EXISTING PLAN list shown below; the Planner will append
               source_rule_ids to that operation.

Inputs:
  1. CANDIDATE (path, method) — inferred from the rules' openapi_mapping.
  2. GROUPED RULES            — every residual rule pointing here.
  3. EXISTING PLAN            — operations already produced by Pass 1
                                (and earlier Pass 2 groups). Use their
                                indices for 'attach'.
  4. 3GPP SPEC CHUNKS         — supporting RAG passages.
  5. OPENAPI REFERENCE        — OpenAPI 3.0 RAG excerpts.

Rules:
  - source_rule_ids must be a subset of the grouped rule indices.
  - For 'new': path/method should mirror the candidate unless the rules
    clearly disagree (mention it in rationale).
  - For 'attach': set attach_to_index to a valid plan index; ignore
    path/method/priority.
  - Never invent rule indices.
"""

_PASS2_USER = """\
CANDIDATE (path, method): {op_label}

GROUPED RULES (residual rules attributed to this candidate):
{grouped_rules}

EXISTING PLAN (operations already chosen — use their index for 'attach'):
{existing_plan}

3GPP SPEC CHUNKS:
{spec_rag}

OPENAPI 3.0 REFERENCE CHUNKS:
{openapi_reference}

Produce the PlannerPass2Op now.
"""

pass2_prompt = ChatPromptTemplate.from_messages([
    ("system", _PASS2_SYSTEM),
    ("human", _PASS2_USER),
])


# ── PASS 2 — schema-property group ───────────────────────────────────────────

_PASS2_SCHEMA_SYSTEM = """\
You are an expert in 3GPP technical specifications and OpenAPI 3.0 design.

You receive one schema name (under components.schemas) and every residual
rule (rule_type='schema_property') that describes its properties. None of
these rules is tied to a (path, method) directly — they describe a
reusable shape.

Your task is to point out WHICH operations in the CURRENT PLAN use this
schema (typically referenced via $ref under requestBody, responses, or
parameters). The Planner will append the rule indices to every operation
you pick.

A schema counts as used by an operation BOTH when the operation references it
directly and when it is reached through another schema the operation already
carries. Each plan entry lists, under "schemas carried", the schemas its rules
already define — follow that chain: if an operation carries schema A, and A has
a property typed by this schema, then this schema is used by that operation.
Reachability at any depth counts: a type used by a type used by an operation is
still used by that operation.

Rules:
  - Use ONLY indices that appear in the CURRENT PLAN list.
  - Return an empty list ONLY when no operation reaches the schema directly or
    through the chain above. The Planner records such rules as gaps, so an empty
    list discards them — prefer attaching when the chain is plausible.
  - Do NOT invent indices.
"""

_PASS2_SCHEMA_USER = """\
SCHEMA NAME: {schema_name}

SCHEMA-PROPERTY RULES (all describe properties of {schema_name}):
{schema_rules}

CURRENT PLAN (use these indices for attach_to_indices):
{existing_plan}

OPENAPI 3.0 REFERENCE CHUNKS (syntax authority):
{openapi_reference}

Produce the PlannerSchemaAttachment now.
"""

pass2_schema_prompt = ChatPromptTemplate.from_messages([
    ("system", _PASS2_SCHEMA_SYSTEM),
    ("human", _PASS2_SCHEMA_USER),
])


# ── PASS 2 — ungrouped epilogue ──────────────────────────────────────────────

_PASS2_UNGROUPED_SYSTEM = """\
You are an expert in 3GPP technical specifications and OpenAPI 3.0 design.

The pipeline has rules whose openapi_object did NOT expose a (path, method)
(typically schema-only rules under components/schemas/<Name>). They were
not handled by Pass 1 (the legacy review) nor by the Pass 2 grouping.

Your task is to attach each ungrouped rule to the operation in the
CURRENT PLAN whose request body / response / parameters most plausibly
reference the schema or topic the rule describes.

For every rule, return an Entry:
  - rule_index       — the rule's index from the UNGROUPED RULES list.
  - attach_to_index  — index in the CURRENT PLAN to attach the rule to,
                       or -1 if no operation is a reasonable fit (the
                       Planner will record the rule as a probable gap).

Never invent rule indices. Never invent plan indices.
"""

_PASS2_UNGROUPED_USER = """\
UNGROUPED RULES (need a home):
{ungrouped_rules}

CURRENT PLAN (use these indices for attach_to_index):
{existing_plan}

Produce the PlannerUngroupedMapping now. Cover every ungrouped rule.
"""

pass2_ungrouped_prompt = ChatPromptTemplate.from_messages([
    ("system", _PASS2_UNGROUPED_SYSTEM),
    ("human", _PASS2_UNGROUPED_USER),
])
