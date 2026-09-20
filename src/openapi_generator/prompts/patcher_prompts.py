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
                           in TargetOperation.source_rule_ids. Each rule is
                           tagged VALIDATED or DISPUTED by the Patcher's gate
                           (nodes.patcher._rule_verdict); rules the gate
                           dropped never appear here.
    legacy_fragment      — pretty-printed legacy YAML fragment for this
                           (path, method) when action != 'create'; empty
                           string otherwise.
    document_so_far      — the document the calls before this one have built,
                           whole. Operations are written one per call into a
                           shared document, and this is what lets the model
                           check rather than assume: whether a schema is
                           defined, whether a parameter is already on the path.
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

Your task is to produce an OpenAPI fragment for ONE operation (path + HTTP
method). An Assembler merges your fragment into a document that already
exists, so two different things are in front of you and they must not be
confused.

  THE DOCUMENT SO FAR — everything written by the calls before yours. It is
  there TO READ, never to copy. You do not return it, restate it, or repeat
  any part of it. Read it to answer questions about what is already settled:
  is this schema defined? is this parameter already on the path? what shape
  did the operations before mine take?

  YOUR FRAGMENT — what this one operation ADDS. Only that. Your fragment is
  merged INTO the document: what you put under `paths` is merged into the
  document's `paths`, and what you put under `components` is merged into the
  document's `components`. So a copy of the document inside your fragment
  does not land beside the original — it lands one level down, as a path
  named `components` or a path named `get`, which is not a path at all and
  breaks the document.

  Returning nothing but the new operation is the correct outcome when the
  document already holds everything else this operation needs.

OUTPUT FORMAT (returned via structured output as OperationFragment):
  - path        : exact path template (must equal the target path).
  - method      : lowercase HTTP method (must equal the target method).
  - paths       : a dict with ONE key, the target path, shaped as
        {{ "<path>": {{ "<method>": <full OpenAPI operation object> }} }}
    The operation object includes summary, description, parameters,
    requestBody (when applicable), responses, security (when applicable),
    and operationId.

    The only keys allowed in this dict are the target path, and inside it
    the target method plus `parameters`. Anything else is a mistake with a
    recognisable shape: `components` here becomes a path named "components",
    and a bare `get` here becomes a path named "get". If you are about to
    write a key that is not the target path, stop — it does not belong in
    this field.

    A PARAMETER GOES AT THE LEVEL ITS RULE ADDRESSES, AND ONLY THERE.
    A rule targeting the PATH (`paths.<path>`) describes a parameter every
    method shares, and it belongs beside the methods:
        {{ "<path>": {{ "parameters": [...], "<method>": {{...}} }} }}
    A rule targeting the OPERATION (`paths.<path>.<method>`) describes one
    that method alone takes, and it goes inside that method's object.

    Never both, and never twice. A parameter declared on the path is already
    in force for every method under it, so repeating it inside an operation
    adds nothing and shifts the positions of the parameters after it. You are
    one call among several writing into the same document: before adding a
    parameter to the path, check "The document so far" — an earlier call may
    have written it already, and then there is nothing for you to add.
  - components  : a dict shaped as
        {{ "schemas": {{ "<SchemaName>": <JSON-Schema object>, ... }} }}
    holding ONLY the schemas THIS call defines for the first time.

    Include a schema here when a rule given to you names it as its target
    (`components/schemas/<Name>`), or when this operation references it and
    the document does not define it yet. A rule reaching you means the
    Planner decided this schema belongs in the document, so define it even
    when no operation references it: `components.schemas` is a sibling of
    `paths`, not an appendix to it, and a specification routinely declares a
    public type (an enumeration, for instance) that no operation happens to
    use. Omitting such a type loses it from the document entirely.

    Leave a schema out when the document already defines it — point a $ref
    at it instead. Copying a definition the document already has is not
    harmless duplication: it is how a whole `components` block ends up
    nested under `paths`. This field is empty whenever the operation needs
    no new schema, and empty is a perfectly good answer.

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
    and the document already defines `<Name>`, reuse the ref without
    redefining.
  - Use the 3GPP RAG context (if provided) to disambiguate types and
    descriptions, NEVER as a source of brand-new constructs not implied by
    a rule.

RULE STATUS — each rule carries a `status` line:
  - VALIDATED — the rules bank validated it. Treat it as authoritative.
  - DISPUTED  — the bank's validator objected but the rule was kept for you
                to arbitrate. It also carries `objection` (why the validator
                rejected it) and `defence` (why the extractor believed it).
                Decide using the spec context: apply the rule only if the
                defence holds up. When you leave a DISPUTED rule out, simply
                omit it — never emit a placeholder or a commented-out stub.

$ref IS EXCLUSIVE:
  - An object holding a `$ref` is a Reference Object, and the OpenAPI 3.0
    specification states it "cannot be extended with additional properties and
    any properties added SHALL be ignored". A `description`, `type`, `format`
    or `example` written beside a `$ref` is therefore dead text: no conforming
    parser reads it.
  - Wrong:  {{ "$ref": "#/components/schemas/X", "description": "..." }}
    Right:  {{ "$ref": "#/components/schemas/X" }}
  - So a property whose type is a named schema is written as the bare `$ref`
    alone. The wording belongs in the referenced schema, where it is read once
    and applies to every use.
  - Wrapping the reference in `allOf` IS the conforming way to attach a
    sibling keyword, because the outer object is then a Schema Object rather
    than a Reference Object. Use it only when a rule states something about
    that specific usage that the referenced schema does not already carry —
    never merely to repeat a description.

EXTERNAL REFERENCES (other 3GPP spec files):
  - A rule value like "$ref: 'TS28623_ComDefs.yaml#/components/schemas/Float'"
    points at a type defined in ANOTHER document. Emit that `$ref` string
    verbatim, exactly as the rule writes it — same file name, same fragment
    path. Never rewrite it to a local '#/components/schemas/...' ref, and
    never invent a local copy of the external schema under `components`.
    Such a type is external by design: it must NOT appear in your
    `components.schemas` output.
  - Only a type this document itself defines belongs in `components.schemas`.

SCHEMA COMPOSITION (allOf / oneOf / anyOf):
  - A rule whose `openapi_field` is `allOf` means the schema INHERITS from the
    referenced type. Build it as a two-member composition — the inherited
    `$ref` first, then a single inline `type: object` member holding the
    schema's own properties:
        <SchemaName>:
          allOf:
            - $ref: '<ref exactly as the rule gives it>'
            - type: object
              properties: {{ ...own properties only... }}
  - Properties inherited through that `$ref` MUST NOT be repeated as own
    properties. If a rule tries to redefine an inherited field, drop it.
  - A rule whose `openapi_field` is `oneOf`/`anyOf` gives the full member
    list as its value; emit the members in the order given, preserving each
    member's own form (`type: ...` inline, or `$ref: ...` for a named type).
  - When several rules target the SAME schema and the SAME composition or
    `enum` field, MERGE their values into one list in the order the rules
    appear — do not emit the field more than once and do not let a later
    rule overwrite an earlier one.
  - Emit a `required` list only when a rule states it; place it inside the
    inline `type: object` member that owns those properties.

DESCRIPTIONS. Write one on each response, and on the operation's summary and
description. Write one nowhere else — a schema property carries no description,
whatever its rule says about it: the property name and its type already state
what the rule states, and a $ref ignores anything beside it. These rules govern
the wording of the ones you do write:
  - Write from the rule's `text`, never from its `value`. The value is the
    construct to emit; the text is what to say about it.
  - Say everything that sentence says, and say it as the outcome rather than as
    a report on the operation: "Success case. The notification was delivered;
    no message body is returned" — not "The POST operation returns 204".
  - Say what happened, not what the field is called. `4XX` already says 4XX.
  - Every response description must differ from every other in the operation.
    Each status code is a distinct outcome, so each says what distinguishes it
    from all the rest — drawing on what the code itself means under RFC 7231,
    which the OpenAPI reference names as the authority on status codes.
  - Add nothing the rule does not state.

RULE TYPES:
Each rule in APPLICABLE RULES has a `type`. The type tells you what that one
rule defines, where it goes in the output, and how to write it. Apply each
rule according to its own type:

{rule_type_guide}

QUALITY GATES — do not output:
  - Any key under `paths` other than the target path.
  - A `components` key inside `paths`, at any depth.
  - Operations whose path or method disagrees with the target_op.
  - A schema the document already defines.
  - A parameter the document already declares on this path.
  - Responses without a schema reference or inline schema.
  - Path parameters absent from the parameters list.
  - A local schema standing in for a type referenced from another spec file.
  - Own properties that duplicate what an `allOf` $ref already contributes.
  - Any key sitting next to a `$ref` in the same object.
"""

_USER = """\
TARGET OPERATION:
{target_op_summary}

APPLICABLE RULES (each grounds part of this operation):
{applicable_rules}

LEGACY FRAGMENT (empty when action='create'):
{legacy_fragment}

THE DOCUMENT SO FAR — READ ONLY. This is what earlier calls already wrote.
Consult it to see what is already defined; never copy any of it into your
answer, and never return it back:
{document_so_far}

3GPP CONTEXT (optional supporting material from RAG; may be empty):
{rag_context}

OPENAPI 3.0 REFERENCE CHUNKS (authoritative spec excerpts via RAG; may be empty):
{openapi_reference}

{correction_task}

Produce the OperationFragment now. Remember:
  - Return ONLY what this operation adds. The document above is context you
    read, not content you return.
  - `paths` holds exactly one key: the target path. No `components` inside it.
  - `components` holds only schemas the document does not define yet; empty
    is the right answer when it defines them all.
  - `path` and `method` MUST match the target exactly.
  - Cover every applicable rule — including one defining a schema no
    operation references; it still belongs under components.schemas.
"""

patcher_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _USER),
])


# ── correction pass ───────────────────────────────────────────────────────
# After every operation is assembled, the Reflector reviews the document and
# the Validator turns what it confirmed into fixes. They all arrive together
# and one call applies them all, because some depend on each other — a schema
# added and the $ref pointing at it.
#
# The call reads the whole document and returns only the blocks it corrected.
# Returning the whole document is what this replaced: every untouched line a
# model restates is a line it can quietly alter, and the cost grew with the
# document until a round stopped fitting in its timeout and returned nothing
# at all.

_CORRECTION_SYSTEM = """\
You are an expert in 3GPP technical specifications and OpenAPI 3.0 design.

You are given the whole generated OpenAPI document and a list of fixes to
apply to it.

READ ALL OF IT — the fixes are written against this document, and judging one
means seeing what surrounds it: the schema a $ref points at, the responses a
sibling operation declares, the conventions the rest of the document follows.
The document below is your context, entire and unabridged.

RETURN ONLY THE BLOCKS YOU CHANGED. What you return is merged back into that
same document, block by block: a path item you return replaces that path
item, a schema you return replaces that schema, and everything you leave out
stays exactly as it is. So return the path items you touched — within each,
only the methods you touched — and the schemas you changed or added. A fix
touching one response means returning that one operation, not the path beside
it and not the schemas below it.

Leaving a part out is what protects it. A part you restate is a part you
might restate differently, and the document already holds work that was
reviewed and judged correct.

You are not reviewing the document and not improving it. The review is
finished, and anything the fixes do not mention has been accepted as it
stands.

Each fix names an action and a place:
  change — the place exists and is wrong. Make it what the instruction says.
  add    — the place is missing. Insert it, in the shape the instruction gives.
  remove — the place exists and does not belong. Take it out, and nothing else.

Apply every fix. When one cannot be applied — the place is not where the fix
says, or the instruction contradicts the document — leave that part untouched
rather than guessing; a later round can revisit it.

NAME EVERY REMOVAL IN `removed`, one dotted path per place taken out, naming
exactly what went: `paths./x.put.responses.404` removes that response, not the
operation holding it. Leaving something out of the blocks you return does NOT
remove it — anything absent is kept as it was. This is deliberate, so that
returning only the part you touched cannot silently delete the rest. A removal
you do not name does not happen.

Removal is the one action that destroys work, so weigh what a fix would cost
against the problem it solves. Removing the place a fix names is right when the
place is itself what is wrong. It is wrong when the problem sits inside
something larger and the instruction points at the container: taking out an
operation to correct one of its responses, or a schema to correct one of its
properties, discards everything else that was right there — and a fix aimed at
a leaf is about that leaf. When a removal would cost more than the problem is
worth, do not perform it: record it in `skipped` with the reason, and say what
the narrower place would have been.

The rules and reference material below are the same ones the document was built
from. Use them to write what a fix asks for, and follow the same conventions
that govern the rest of the document: a $ref stands alone, an external $ref is
copied verbatim, a composition keeps its two-member shape.

Return the blocks you changed, and nothing else.
"""

_CORRECTION_USER = """\
THE DOCUMENT AS IT STANDS — the complete OpenAPI built so far. This is the
document the fixes below were written against, and the one your output is
merged back into:
{document_yaml}

FIXES TO APPLY — each names a place in the document above and what to do
there:
{fixes}

THE RULES THE DOCUMENT WAS BUILT FROM — the 3GPP requirements behind it, for
writing what a fix asks for:
{applicable_rules}

OPENAPI 3.0 REFERENCE — excerpts of the OpenAPI specification itself (may be
empty):
{openapi_reference}

NOW APPLY THE FIXES AND OUTPUT THE CORRECTED BLOCKS — ONLY THOSE, NOTHING ELSE.
A block is one path item or one schema. Output a block only if a fix changed
it; output it corrected, complete and in full. Output NO block that no fix
touched — not to confirm it, not to restate it, not for completeness. Those
blocks already exist in the document and are kept exactly as they are.

  - `paths`: only the path items you corrected, and within each only the
    methods you corrected.
  - `components`: only the schemas you corrected or added.
  - `removed`: the dotted path of each place you took out.
  - `applied` / `skipped`: which fixes you carried out, and which you did not
    and why.
"""

patcher_correction_prompt = ChatPromptTemplate.from_messages([
    ("system", _CORRECTION_SYSTEM),
    ("human", _CORRECTION_USER),
])


# ── servers pass ──────────────────────────────────────────────────────────
# Runs once per document, and only when no rule in the bank defines `servers`.
# Reading a specification for where a service is hosted is a judgement task:
# one service states a URI template, another says only that its address comes
# from a subscription, and a pattern written for the first shape answers the
# second with a sibling service's prefix — plausible and wrong. Hence an LLM
# over the URI clauses plus RAG, rather than a parser.

_SERVERS_SYSTEM = """\
You are an expert in 3GPP technical specifications and OpenAPI 3.0 design.

Determine the `servers` entry for ONE OpenAPI document: where the service it
describes is hosted.

A resource URI splits in two. The leading part locates the SERVICE and belongs
in `servers.url`; the trailing part identifies a RESOURCE and belongs in
`paths`. The specification itself marks the boundary in how it defines each URI
variable: one defined BY REFERENCE to another clause or document (wordings such
as "See clause 4.4.2 of TS 32.158") is infrastructure and belongs to the server,
while one explained in prose ("Identifier of the targeted resource") identifies
a resource and stays in the path. Cut after the last infrastructure variable.

The paths already generated for this document tell you WHICH service it is
about — a specification often defines several, each with its own server. Prefer
the resource URI whose trailing part matches one of those paths. Ignore URIs
belonging to other services, however similar.

Return url="" when the specification declares no server for this service. That
is a real case, not a failure: a notification sink hosted on the CONSUMER side,
whose address arrives through a subscription, states no server the producer
could name. Saying so is right; borrowing another service's prefix is not.

Ground every part of your answer in the passages given. Never invent a
hostname, a version, or a variable the specification does not name. Leave a
variable's `default` empty unless the text states a value.

Write `rationale` and `description` in English, whatever language the
specification is written in: both are carried into the published document —
`rationale` becomes the note explaining a placeholder to whoever opens the
YAML — and an OpenAPI document is read by an international audience. Keep the
rationale to a few sentences, and say plainly which passage settled the
question, or why the specification states no server.
"""

_SERVERS_USER = """\
PATHS ALREADY IN THIS DOCUMENT:
{document_paths}

RESOURCE URI CLAUSES FOUND IN THE SPECIFICATION:
{uri_clauses}

3GPP CONTEXT (retrieved passages; may be empty):
{rag_context}

Produce the PatcherServerDecision now.
"""

patcher_servers_prompt = ChatPromptTemplate.from_messages([
    ("system", _SERVERS_SYSTEM),
    ("human", _SERVERS_USER),
])
