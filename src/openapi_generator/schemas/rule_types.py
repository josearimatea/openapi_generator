"""
The rule taxonomy the rules bank emits, and where each type lands in OpenAPI.

Single source of truth for the pipeline. The types come from openapi_rulesbank's
RawRule.rule_type; every node that reasons about a rule — the Planner routing
it, the Patcher rendering it, the prompts describing it — reads them from here
rather than repeating a list that drifts out of step.

Two fields carry the placement, and both are needed:

    rule_type        which OpenAPI construct the rule describes
    openapi_mapping  where it lands: {openapi_object, openapi_field,
                     openapi_value, references}

They are not redundant, because each type writes the address differently. A
`path_operation` puts the path in `openapi_object` and the method in
`openapi_field`; a `response` puts path AND method inside `openapi_object`
(`paths./x.post.responses`) and uses `openapi_field` for the status code. So
`rule_type` is what tells a reader how to parse `openapi_mapping` — neither
field alone is enough.
"""

from typing import Dict, List, NamedTuple, Optional


class RuleTypeSpec(NamedTuple):
    """What one rule type means and where it belongs."""

    source_name: str   # what `source_name` holds for this type
    destination: str   # where the rule lands in the OpenAPI document
    address: str       # how openapi_mapping encodes that destination
    guidance: str      # how the Patcher should render it


RULE_TYPES: Dict[str, RuleTypeSpec] = {
    "path_operation": RuleTypeSpec(
        source_name="IS operation name (createMOI, getMOIAttributes)",
        destination="paths.<path>.<method> — the Operation Object itself",
        address="openapi_object is paths.<path>; openapi_field is the method",
        guidance=(
            "Declares that the operation exists and gives it its identity. "
            "Derive a stable, lowerCamelCase `operationId` from source_name "
            "(the IS operation the spec names), and a summary from rule_text. "
            "One such rule per (path, method)."
        ),
    ),
    "request_body": RuleTypeSpec(
        source_name="IS operation name",
        destination="paths.<path>.<method>.requestBody",
        address="openapi_object carries path and method; openapi_field names the part",
        guidance=(
            "The payload the consumer sends. Emit `content` keyed by the media "
            "type the rule names (application/json unless stated otherwise), "
            "whose `schema` is a $ref to the named type — never an inline copy "
            "of a schema defined elsewhere. Mark it `required: true` unless a "
            "rule says the body is optional."
        ),
    ),
    "response": RuleTypeSpec(
        source_name="IS operation name",
        destination="paths.<path>.<method>.responses.<code>",
        address="openapi_object carries path and method; openapi_field is the status code",
        guidance=(
            "One rule per status code, and openapi_field IS that code. Emit it "
            "exactly as written — '204', a range such as '4XX', or 'default' — "
            "never converting between those forms, since a range and 'default' "
            "are different constructs. Every response needs a `description`. A "
            "response with a body carries `content` with a schema, preferring a "
            "$ref when the schema already exists; a 204 carries none."
        ),
    ),
    "path_parameter": RuleTypeSpec(
        source_name="parameter name (MnSVersion, id)",
        destination="paths.<path>.parameters — applies to every method",
        address="openapi_object is paths.<path>; openapi_field names the parameter",
        guidance=(
            "Always `in: path` and `required: true` — OpenAPI admits no optional "
            "path parameter. The parameter name MUST match the {…} variable in "
            "the path template exactly, and every variable in the path needs "
            "one. Give it a `schema` (type string unless the rule says another)."
        ),
    ),
    "query_parameter": RuleTypeSpec(
        source_name="parameter name (scope, filter)",
        destination="paths.<path>.parameters — applies to every method",
        address="openapi_object is paths.<path>; openapi_field names the parameter",
        guidance=(
            "`in: query`, and optional unless a rule states it is required. "
            "Give it a `schema`; when the rule names a type defined in the "
            "document, point at it with a $ref rather than restating it."
        ),
    ),
    "schema_property": RuleTypeSpec(
        source_name="NRM attribute name, or the composition keyword",
        destination="components.schemas.<Name>",
        address=(
            "openapi_object is components/schemas/<Name>; openapi_field is the "
            "property or the keyword"
        ),
        guidance=(
            "openapi_field is either properties.<name> for a single property, or "
            "a keyword — allOf, oneOf, anyOf, enum, required — describing the "
            "schema as a whole. Rules sharing a schema and a keyword are merged "
            "into one list, never emitted twice."
        ),
    ),
    "callback": RuleTypeSpec(
        source_name="notification name (notifyMOICreation)",
        destination="paths.<path>.<method>.callbacks.<name>",
        address=(
            "openapi_object may hold the whole callback chain "
            "(paths.<path>.<method>.callbacks.<name>…), or only the registering "
            "path with the method in openapi_field"
        ),
        guidance=(
            "An out-of-band notification the producer sends back to the "
            "consumer. It belongs INSIDE the operation that registers it, keyed "
            "by notification name under `callbacks`, with a runtime expression "
            "such as '{$request.body#/notificationRecipientAddress}' as the URL. "
            "It is never a path of its own: emitting one as a top-level path "
            "yields a URL full of dots and braces that no server can route."
        ),
    ),
    "security_scheme": RuleTypeSpec(
        source_name="scheme name (OAuth2, BearerAuth)",
        destination="components.securitySchemes.<Name>",
        address="openapi_object is components/securitySchemes/<Name>",
        guidance=(
            "A document-level component. Define it under components; reference "
            "it from an operation's `security` only when a rule says that "
            "operation requires it."
        ),
    ),
}

KNOWN_RULE_TYPES = frozenset(RULE_TYPES)


def format_for_prompt(rule_types: Optional[List[str]] = None) -> str:
    """Render the taxonomy for an LLM prompt.

    Pass the types actually present in the rules at hand so the prompt carries
    only what matters; omit to describe all of them. Unknown names are skipped
    rather than raising — a rules bank may add a type before this catches up,
    and the prompt is better short than broken.
    """
    names = list(RULE_TYPES) if rule_types is None else rule_types
    seen: set = set()
    lines: List[str] = []
    for name in names:
        spec = RULE_TYPES.get(name)
        if spec is None or name in seen:
            continue
        seen.add(name)
        lines.append(
            f"  {name}\n"
            f"      lands in : {spec.destination}\n"
            f"      how      : {spec.guidance}"
        )
    return "\n".join(lines) if lines else "  (no recognised rule type in this set)"
