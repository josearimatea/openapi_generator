"""
Derive the `servers` block, which the generator owns end to end.

`servers` is deliberately not a rules-bank concern: it says where a service is
hosted, which is a property of the document being built rather than a rule
extracted from a clause, and pushing it upstream would load the bank with work
that belongs here. So the generator settles it in two steps:

  1. read the specification — an LLM call over the URI clauses gathered by
     `uri_clauses` plus RAG context;
  2. failing that, a marked placeholder from `placeholder_servers`, so the
     document always declares a server and the gap stays visible.

What lives here is the context step 1 reads plus the placeholder that closes
step 2. Deciding the server is a reading task, not a
parsing one: one service states its address as a URI template, another says
only that the address "is provided by the notification subscription", and a
pattern written for the first shape answers the second either not at all or,
worse, with a sibling service's prefix — plausible and wrong. So the passages
gathered here are evidence, weighed by the LLM together with what the 3GPP RAG
retrieves.

`uri_clauses` collects from normative clauses, never an annex: a 3GPP spec
typically closes with the finished OpenAPI documents, and lifting the answer
from there would make the pipeline copy its own target rather than derive it.

What it gathers, for each resource URI the specification states, is the
template plus the definitions of its variables. Those definitions are what
separate server from path, and the specification already makes the
distinction: a variable defined BY REFERENCE TO ANOTHER CLAUSE OR DOCUMENT is
infrastructure — it says where the service lives — while one explained in
prose identifies a resource. Purely to illustrate the shape (the names and
numbers are incidental, nothing here matches them):

    Resource URI: {SomeRoot}/SomeService/{SomeVersion}/things/{thingId}

    Table N: URI variables
      SomeRoot      See clause 4.4.3 of TS 32.158 [15]   <- refers out
      SomeVersion   See clause 4.4.3 of TS 32.158 [15]   <- refers out
      thingId       Identifier of the targeted thing     <- prose

Read that way, `{SomeRoot}/SomeService/{SomeVersion}` is the server and
`/things/{thingId}` the path — but it is the LLM that reads it, since a
specification is free to describe the same thing another way.
"""

import re
from typing import Any, Dict, List, Optional

from openapi_generator.config import get_logger

logger = get_logger(__name__)

# "Resource URI:" then the template, on the same line or the next one.
_RESOURCE_URI = re.compile(r"Resource URI:[ \t]*\n?[ \t]*(\S[^\n]*)", re.IGNORECASE)

# Where the annexes begin; everything from there on is out of bounds.
_ANNEX_START = re.compile(r"^#*\s*Annex\s+[A-Z]\b", re.IGNORECASE | re.MULTILINE)


def _normative_text(spec_text: str) -> str:
    """The specification with its annexes removed."""
    matches = list(_ANNEX_START.finditer(spec_text))
    return spec_text[: matches[-1].start()] if matches else spec_text


def _uri_variables(spec_text: str) -> Dict[str, str]:
    """Every URI variable named in a 'URI variables' table, mapped to its definition."""
    variables: Dict[str, str] = {}
    for table in re.finditer(
        r"URI variables(.*?)(?=\n\s*\n\s*\S+\s*\n\s*-{3,}|\Z)",
        spec_text,
        re.DOTALL | re.IGNORECASE,
    ):
        for line in table.group(1).splitlines():
            row = re.match(r"\s{2,}(\S+)\s{2,}(\S.*?)\s*$", line)
            if row and not row.group(1).startswith(("-", "*", "|")):
                variables.setdefault(row.group(1), row.group(2))
    return variables


def _clean(text: str) -> str:
    """Undo the Markdown escaping the converted specs carry (\\[15\\] -> [15])."""
    return re.sub(r"\\([\[\]()*_])", r"\1", text).strip()


def uri_clauses(spec_text: str, document_paths: Optional[List[str]] = None) -> str:
    """Gather the specification passages that bear on where the service lives.

    This only COLLECTS; it decides nothing. A regex can find a clause laid out
    the way this drafting convention expects, but it cannot read a service
    whose address is described in prose — the sink whose URI "is provided by
    the notification subscription" states no template to match, and a pattern
    looking for templates will either miss it or, worse, latch onto a sibling
    service's URI and hand back a plausible wrong answer. So the passages found
    here are evidence for the LLM to weigh alongside what the RAG retrieves,
    not a conclusion.

    When `document_paths` are given, clauses whose URI ends with one of them
    come first: in a specification defining several services, that is the
    cheapest signal for which service this document is about. Matching by
    suffix needs no list of service names and does not care how many services
    the document defines.
    """
    body = _normative_text(spec_text)
    variables = _uri_variables(body)

    matched: List[str] = []
    others: List[str] = []
    for match in _RESOURCE_URI.finditer(body):
        template = match.group(1).strip()
        if not template.startswith(("{", "/")):
            continue
        used = [
            f"      {name} — {_clean(definition)}"
            for name, definition in variables.items()
            if "{" + name + "}" in template
        ]
        entry = f"  Resource URI: {template}"
        if used:
            entry += "\n    URI variables:\n" + "\n".join(used)
        belongs = any(
            template.rstrip("/").endswith(path.rstrip("/"))
            for path in (document_paths or [])
        )
        (matched if belongs else others).append(entry)

    lines: List[str] = []
    if matched:
        lines.append("Resource URIs whose tail matches a path in this document:")
        lines.extend(matched)
    if others:
        lines.append("Other resource URIs stated in the specification:")
        lines.extend(others[:12])
    return "\n".join(lines) if lines else "(the specification states no resource URI template)"


def document_metadata(spec_text: str, llm=None) -> Dict[str, Any]:
    """Read `info` and `externalDocs` values off the specification's cover page.

    The cover states the document number, its version, its subject and the
    copyright outright, so no rule is needed for them — but a source arrives as
    PDF or DOCX and the conversion to Markdown lays the same page out
    differently each time (a table of cells, or loose lines with arbitrary
    breaks). The values are always written; their position is not. So the page
    is read by an LLM rather than matched against a pattern, which would work
    for one converter's output and fail silently on another's.

    Returns the fields the page states, plus the archive URL derived from the
    number. An empty dict when nothing could be read — the caller then leaves
    those blocks out rather than inventing them.
    """
    cover = spec_text[:8000]
    if not cover.strip():
        return {}

    if llm is None:
        from openapi_generator.config.llm_config import get_llm
        llm = get_llm()

    from openapi_generator.prompts.loader_prompts import loader_metadata_prompt
    from openapi_generator.schemas.operation import LoaderDocumentMetadata

    try:
        chain = loader_metadata_prompt | llm.with_structured_output(
            LoaderDocumentMetadata, method="function_calling"
        )
        read: LoaderDocumentMetadata = chain.invoke({"cover_page": cover})
    except Exception as e:
        logger.warning(f"cover-page pass failed ({type(e).__name__}: {e})")
        return {}

    out: Dict[str, Any] = {
        key: value
        for key, value in (
            ("number", read.number.strip()),
            ("version", read.version.strip()),
            ("release", read.release.strip()),
            ("subject", read.subject.strip()),
            ("copyright", " ".join(read.copyright.split())),
        )
        if value
    }

    number = out.get("number", "")
    if re.fullmatch(r"\d{2}\.\d{3}", number):
        series = number.split(".")[0]
        # The 3GPP archive lays its specifications out by series, always so.
        out["external_docs_url"] = (
            f"http://www.3gpp.org/ftp/Specs/archive/{series}_series/{number}/"
        )
        if out.get("subject"):
            out["external_docs_description"] = f"3GPP TS {number}; {out['subject']}"

    logger.info(f"cover page read: TS {number or '?'} v{out.get('version', '?')}")
    return out


def placeholder_servers(explanation: str = "") -> Dict[str, Any]:
    """A Server Object left blank, for when the specification yields no server.

    The block keeps the shape a Server Object has — `url` and `description`,
    nothing else — with the url empty because there is no value to state.
    Inventing a `{server}` template with variables of its own would dress a
    gap up as a decision, and a reader would have to work out that the URL
    means nothing.

    `description` carries the fixed statement that the server could not be
    determined — the same wording every time, so it reads as the tool speaking.
    The reasoning from the pass that looked goes in `x-openapi-gen-note`,
    apart: it was written by a language model, and a reader deserves to know
    which half of the block is a fixed message and which is a model's account
    of why the value is missing. Between them the gap is legible in the
    document itself, without consulting a log.
    """
    server: Dict[str, Any] = {
        "url": "",
        "description": "The server could not be determined from the specification.",
    }
    if explanation:
        server["x-openapi-gen-note"] = explanation
    return server
