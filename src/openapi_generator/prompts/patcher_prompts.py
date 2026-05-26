"""
Prompt template for the Patcher node.

The Patcher receives ONE TargetOperation (path, method, action, rules)
and produces a complete OpenAPI fragment for it: the operation block under
`paths.<path>.<method>` plus any new schemas referenced under
`components.schemas`.

Inputs interpolated into the prompt:
    target_op_summary    — one-line description of the (path, method, action).
    applicable_rules     — full text of every rule grounding this operation,
                           taken from rules_bank["rules"] using the indices
                           in TargetOperation.source_rule_ids.
    legacy_fragment      — pretty-printed legacy YAML fragment for this
                           (path, method) when action != 'create'; empty
                           string otherwise.
    existing_schemas     — names of schemas already in final_openapi so the
                           Patcher reuses them via $ref instead of redefining.
    rag_context          — 3GPP chunks retrieved via RAG; empty string when
                           RAG is unavailable.
    openapi_reference    — top-k chunks retrieved from the OpenAPI 3.0
                           reference collection in Qdrant (see
                           tools/rag_tools.search_openapi_reference);
                           empty string when the collection is missing.
    correction_task      — populated only on retry; the Validator's
                           per-op feedback from the previous iteration.
"""

from langchain_core.prompts import ChatPromptTemplate

_SYSTEM = """\
You are an expert in 3GPP technical specifications and OpenAPI 3.0 design.

Your task is to produce a complete OpenAPI fragment for ONE operation
(path + HTTP method). The fragment will be merged into a larger OpenAPI
document by an Assembler node, so it MUST be self-contained and valid in
isolation.

OUTPUT FORMAT (returned via structured output as OperationFragment):
  - path        : exact path template (must equal the target path).
  - method      : lowercase HTTP method (must equal the target method).
  - paths       : a dict shaped as
        {{ "<path>": {{ "<method>": <full OpenAPI operation object> }} }}
    The operation object includes summary, description, parameters,
    requestBody (when applicable), responses, security (when applicable),
    and operationId.
  - components  : a dict shaped as
        {{ "schemas": {{ "<SchemaName>": <JSON-Schema object>, ... }} }}
    Include ONLY new schemas referenced by this operation. If a schema
    already exists in the document (see "Existing schemas" below), use
    $ref instead of redefining it.

ACTIONS — interpret the target_op.action:
  - 'create' — build the operation from scratch using ONLY the applicable
               rules and the 3GPP context provided. There is no legacy
               fragment.
  - 'update' — start from the legacy fragment shown below, then apply the
               changes implied by the rules (new parameters, new responses,
               schema fields, etc.). Preserve any legacy detail that is
               not contradicted by a rule.
  - 'keep'   — return the legacy fragment unchanged in the `paths` field.
               Include only schemas the legacy fragment already references.

GROUNDING:
  - Every parameter, response, request body, and schema property in the
    output MUST be justified by one of the applicable rules OR by the
    legacy fragment (for 'update'/'keep'). Do not invent fields.
  - If a rule's `openapi_value` is "$ref: '#/components/schemas/<Name>'"
    and `<Name>` is in "Existing schemas", reuse the ref without redefining.
  - Use the 3GPP RAG context (if provided) to disambiguate types and
    descriptions, NEVER as a source of brand-new constructs not implied by
    a rule.

PATH PARAMETERS:
  - Every path variable enclosed in `{{...}}` in the path MUST appear as
    a parameter with `in: path`, `required: true`, and a schema.
  - The variable name in the parameter must match the variable in the path.

RESPONSES:
  - Always include at least one success response (2XX) and at least one
    error response (4XX or 5XX, or "default") if rules suggest it.
  - Use `$ref` for response bodies whose schemas already exist.

OPERATION ID:
  - Provide a stable, lowerCamelCase `operationId` derived from the
    operation's primary IS source_name (visible in the applicable rules).

QUALITY GATES — do not output:
  - Operations whose path or method disagrees with the target_op.
  - Schemas you defined inline when the same name exists in
    "Existing schemas".
  - Responses without a schema reference or inline schema.
  - Path parameters absent from the parameters list.
"""

_USER = """\
TARGET OPERATION:
{target_op_summary}

APPLICABLE RULES (each grounds part of this operation):
{applicable_rules}

LEGACY FRAGMENT (empty when action='create'):
{legacy_fragment}

EXISTING SCHEMAS (already in the document — reuse via $ref):
{existing_schemas}

3GPP CONTEXT (optional supporting material from RAG; may be empty):
{rag_context}

OPENAPI 3.0 REFERENCE CHUNKS (authoritative spec excerpts via RAG; may be empty):
{openapi_reference}

{correction_task}

Produce the OperationFragment now. Remember:
  - `path` and `method` MUST match the target exactly.
  - Cover every applicable rule in the output.
  - Define new schemas only when not already in "Existing schemas".
"""

patcher_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _USER),
])
