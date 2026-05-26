"""
Prompt template for the Planner node.

The Planner takes a flat list of extracted rules (from rules_bank) and an
optional legacy OpenAPI document, and produces an ordered list of
TargetOperation entries — the unit of work for the per-operation loop.

Inputs interpolated into the prompt:
    rules_summary    — compact table of rules indexed by their position in
                       rules_bank["rules"]; the position is the source_rule_id
                       the Planner must reference.
    legacy_summary   — bullet list of (path, method) pairs already present in
                       the legacy OpenAPI, with a short hint per path; empty
                       string when no legacy was provided.
    document_summary — one-line context about what API surface this run covers.
"""

from langchain_core.prompts import ChatPromptTemplate

_SYSTEM = """\
You are an expert in 3GPP technical specifications and OpenAPI 3.0 design.

Your task is to plan the generation of an OpenAPI document. You are given:
  1. A list of RULES already extracted from the 3GPP spec — each rule
     describes one OpenAPI artifact (an HTTP method, a path parameter, a
     query parameter, a request body, a response, a schema property, or a
     security scheme). Each rule has an index — that index is what you must
     reference in `source_rule_ids`.
  2. (Optional) A summary of the LEGACY OpenAPI document. The legacy is the
     starting point: operations already present there should be preserved
     unless the rules require changes.

Produce an `ExtractionPlan` listing every OpenAPI operation that must appear
in the final document. One operation = one (path, method) pair.

For each operation, decide the `action`:
  • 'create' — operation absent from the legacy, must be built from scratch
               using the rules.
  • 'update' — operation present in the legacy AND rules require changes
               (new parameters, response codes, schema fields, etc.).
  • 'keep'   — operation present in the legacy and rules confirm it as-is;
               the Patcher just carries it forward without modifying.

For each operation, list every rule index that grounds it in
`source_rule_ids`. A rule maps to an operation when its
`openapi_mapping.openapi_object` references the same path or schema as the
operation. Several rules may ground the same operation (one for the HTTP
method, one for each parameter, one for each response, one per schema
property of the request/response body, etc.).

MERGING RULES THAT SHARE (path, method):
  OpenAPI allows only ONE entry per (path, method) — you cannot have two
  `put` blocks under the same path. 3GPP, however, often maps several IS
  operations to the same HTTP verb on the same path (e.g. `createMOI` and
  `modifyMOIAttributes` both use PUT on /{{className}}={{id}}: PUT creates
  if the resource does not exist, replaces it if it does).

  When you see multiple rules that resolve to the same (path, method) —
  even if their `openapi_object` strings differ slightly (one may include
  the full server prefix, another only the relative path) and their
  rule_type/source_name differ — emit ONE TargetOperation that covers all
  of them. Put EVERY relevant rule index into `source_rule_ids`. Mention
  in `rationale` that this single OpenAPI operation serves multiple IS
  operations (list their names).

ORDERING RULES:
  • Operations whose request/response schemas are referenced by other
    operations come first (so $refs resolve as the Patcher builds the
    document incrementally).
  • Within the same dependency level: 'high' priority before 'medium'
    before 'low'.
  • No duplicates: each (path, method) appears at most ONCE — never twice.
  • Cover every rule: every index from the rules table should appear in at
    least one operation's `source_rule_ids`. If a rule does not fit any
    operation, surface it by attaching it to the most plausible one and
    note that in `rationale`.

PRIORITY:
  • 'high'   — schemas reused across operations (e.g. shared error
               response, base resource models), or core CRUD entry points.
  • 'medium' — standard CRUD operations on individual resources.
  • 'low'   — auxiliary endpoints (notifications, subscriptions, status,
               etc.).
"""

_USER = """\
DOCUMENT SUMMARY:
{document_summary}

LEGACY OPENAPI (existing (path, method) pairs):
{legacy_summary}

RULES TABLE (each row is one rule):
{rules_summary}

Produce the ExtractionPlan now. Remember:
  - `source_rule_ids` values MUST come from the leftmost `index` column of
    the rules table above. The table tells you the exact valid range
    (e.g. 0..239). Never invent indices, never use any other number you
    might see in the rule text or openapi_object.
  - Cover every rule index at least once across all operations.
  - Order operations so referenced schemas come before referencing ones.
"""

planner_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _USER),
])
