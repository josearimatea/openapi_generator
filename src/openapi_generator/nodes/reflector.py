"""
Reflector — structural checks (no LLM) then two LLM passes over the document.

Runs once, after the Patcher has produced every operation and the Assembler has
merged them, so what it reads is the assembled document rather than a fragment.
It reports what is wrong; it neither rewrites the document nor prescribes fixes.
Turning its findings into instructions is the Validator's job — the account of
a problem is written by whoever noticed it, the fix by whoever answers for the
retry.

Three stages, each narrowing what the next has to think about:

  1. Structural checks, in code. Everything the OpenAPI specification settles on
     its own: a $ref either resolves or it does not, a path variable either has
     a parameter or it does not. These cost nothing and never vary, but they see
     shape rather than intent, so they produce CANDIDATES, not verdicts.

  2. First pass, part by part. Each operation and each schema against the rules
     behind it, with the candidates as context so the pass spends its attention
     on what a checker cannot see — whether the document says what the rules
     meant — instead of rediscovering broken references.

  3. Second pass, over the whole document, given everything found so far. It
     confirms or sets aside each problem raised, and looks for what no single
     part reveals: a rule nothing reflects, something invented, an inconsistency
     between operations. This is also where what to ADD and what to REMOVE is
     decided, since absence and excess are rarely visible from inside one part.

State reads : final_openapi, rules_bank, operations_plan
State writes: fragment_reflection — the confirmed problems, for the Validator
"""

import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

import yaml

from openapi_generator.config import get_logger
from openapi_generator.nodes.patcher import RULE_TYPE_GUIDE, _build_rules_block

logger = get_logger(__name__)

_HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")

# A local reference points inside this document; anything else names another
# file and cannot be resolved from here.
_LOCAL_REF = re.compile(r"^#/components/schemas/([A-Za-z0-9_.-]+)$")

# Path template variables: {className}, {id}, and so on.
_PATH_VARIABLE = re.compile(r"\{([^}]+)\}")


# ── Stage 1 — structural checks, no LLM ─────────────────────────────────────

def _walk(node: Any, at: str = "") -> Iterator[Tuple[str, Any]]:
    """Every node in the document, with its dotted path."""
    yield at, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{at}.{key}" if at else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{at}[{i}]")


def _operations(document: Dict[str, Any]) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
    """Every (path, method, operation object) the document declares."""
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() in _HTTP_METHODS and isinstance(operation, dict):
                yield path, method, operation


def _structural_checks(document: Dict[str, Any]) -> List[Dict[str, str]]:
    """Problems the OpenAPI specification settles without interpretation.

    Candidates for the passes that follow, not conclusions: `_unreferenced`
    in particular flags something a specification does legitimately — declaring
    a public type no operation uses — and exists so the LLM can weigh it.
    """
    problems: List[Dict[str, str]] = []

    def add(where: str, problem: str) -> None:
        problems.append({"where": where, "problem": problem})

    schemas = (document.get("components") or {}).get("schemas") or {}
    referenced: set = set()

    for at, node in _walk(document):
        if not isinstance(node, dict):
            continue

        # A Reference Object carries $ref alone: OpenAPI 3.0 says "any
        # properties added SHALL be ignored", so a sibling is dead text.
        if "$ref" in node and len(node) > 1:
            siblings = ", ".join(sorted(k for k in node if k != "$ref"))
            add(at, f"$ref shares its object with {siblings}; OpenAPI 3.0 ignores "
                    "every key beside a $ref, so those are dropped when read")

        ref = node.get("$ref")
        if isinstance(ref, str):
            match = _LOCAL_REF.match(ref.strip().strip("'\""))
            if match:
                referenced.add(match.group(1))
                if match.group(1) not in schemas:
                    add(at, f"{ref} names a schema this document does not define")

        # A path parameter is required by definition.
        if node.get("in") == "path" and node.get("required") is not True:
            add(at, "a path parameter must be required: true")

        # required names must exist among the same object's properties; when
        # there are none at all they belong to a sibling composition member,
        # which this check cannot see.
        if isinstance(node.get("required"), list) and "properties" in node:
            declared = set(node.get("properties") or {})
            for name in node["required"]:
                if isinstance(name, str) and name not in declared:
                    add(f"{at}.required",
                        f"{name} is required but the object declares no such property")

    for name in sorted(set(schemas) - referenced):
        add(f"components.schemas.{name}",
            "nothing in the document references this schema")

    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        variables = set(_PATH_VARIABLE.findall(path))
        shared = {
            p.get("name") for p in (item.get("parameters") or [])
            if isinstance(p, dict) and p.get("in") == "path"
        }
        for method, operation in item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            declared = shared | {
                p.get("name") for p in (operation.get("parameters") or [])
                if isinstance(p, dict) and p.get("in") == "path"
            }
            for name in sorted(variables - declared):
                add(f"paths.{path}.{method}",
                    f"the path declares {{{name}}} but the operation has no parameter for it")

    seen_ids: Dict[str, List[str]] = {}
    for path, method, operation in _operations(document):
        at = f"paths.{path}.{method}"
        if not (operation.get("responses") or {}):
            add(at, "the operation declares no responses, which OpenAPI requires")
        for code, response in (operation.get("responses") or {}).items():
            if (
                isinstance(response, dict)
                and "$ref" not in response
                and not str(response.get("description") or "").strip()
            ):
                add(f"{at}.responses.{code}",
                    "the response has no description, which OpenAPI requires")
        name = operation.get("operationId")
        if isinstance(name, str) and name:
            seen_ids.setdefault(name, []).append(at)

    for name, places in seen_ids.items():
        if len(places) > 1:
            for at in places:
                add(at, f"operationId {name!r} is used by more than one operation")

    # A composition inherits from the members before it, so a member restating
    # one of their properties is redundant at best, contradictory at worst.
    for name, schema in schemas.items():
        members = schema.get("allOf") if isinstance(schema, dict) else None
        if not isinstance(members, list):
            continue
        seen: Dict[str, int] = {}
        for i, member in enumerate(members):
            if not isinstance(member, dict):
                continue
            for prop in (member.get("properties") or {}):
                if prop in seen:
                    add(f"components.schemas.{name}.allOf[{i}].properties.{prop}",
                        f"{prop} is already declared by an earlier allOf member")
                else:
                    seen[prop] = i

    if problems:
        logger.info(f"Reflector → structural checks: {len(problems)} candidate(s)")
    return problems


# ── Rendering for the prompts ───────────────────────────────────────────────

def _format_problems(problems: List[Dict[str, Any]]) -> str:
    if not problems:
        return "(nothing found)"
    lines = []
    for p in problems:
        line = f"  - {p.get('where', '?')}: {p.get('problem', '?')}"
        if p.get("severity"):
            line += f" [{p['severity']}]"
        lines.append(line)
    return "\n".join(lines)


def _format_rules(rules: List[Dict[str, Any]], plan: List[Dict[str, Any]]) -> str:
    """The rules behind the document, grouped by the operation that received them.

    Rendered the way the Patcher renders them, `rule_text` included. Judging
    whether the document says what a rule meant takes the rule's own sentence:
    a mapping alone shows where a value landed, not what was being asked for,
    and a reviewer without the sentence ends up inventing a standard of its own.
    """
    if not plan:
        return "(no plan available)"
    lines: List[str] = []
    for op in plan:
        label = f"{(op.get('method') or '?').upper()} {op.get('path') or '?'}"
        lines.append(f"  {label}")
        rule_ids = [rid for rid in (op.get("source_rule_ids") or []) if 0 <= rid < len(rules)]
        block, _ = _build_rules_block(rules, rule_ids)
        lines.extend(f"  {line}" for line in block.splitlines())
    return "\n".join(lines)


def document_rag_context(
    document: Dict[str, Any],
    rules: List[Dict[str, Any]],
    plan: List[Dict[str, Any]],
    retriever=None,
) -> Tuple[str, str]:
    """Retrieve what the document under review is about, from both collections.

    Judging a document takes the same two kinds of grounding the Patcher needed
    to write it: the OpenAPI specification, to say whether a construct is
    well-formed, and the 3GPP text, to say whether it means what the
    specification asked for. Neither is asked in the abstract — both queries
    are built from what this document actually contains, so the passages that
    come back are about the operations and types under review rather than about
    OpenAPI in general.

    Returns (3GPP context, OpenAPI reference), each empty when unavailable.
    """
    from openapi_generator.nodes.patcher import _openapi_reference_query

    operations = [f"{m.upper()} {p}" for p, m, _ in _operations(document)]
    schemas = sorted((document.get("components") or {}).get("schemas") or {})

    spec_context = ""
    if retriever is not None and (operations or schemas):
        query = " — ".join(operations[:4] + schemas[:4])
        try:
            spec_context = "\n\n---\n\n".join(retriever(query, k=6) or [])
        except Exception as e:
            logger.warning(f"Reflector → 3GPP RAG failed: {e}")

    reference = ""
    rule_ids = [rid for op in plan for rid in (op.get("source_rule_ids") or [])]
    try:
        from openapi_generator.tools.rag_tools import search_openapi_reference
        reference = search_openapi_reference(
            _openapi_reference_query(rules, rule_ids)
        ) or ""
    except Exception as e:
        logger.warning(f"Reflector → OpenAPI reference RAG failed: {e}")

    return spec_context, reference


def _schemas_reached_by(document: Dict[str, Any], path: str, method: str) -> List[str]:
    """Names of the schemas an operation reaches, following $ref transitively."""
    operation = ((document.get("paths") or {}).get(path) or {}).get(method)
    if operation is None:
        return []
    schemas = (document.get("components") or {}).get("schemas") or {}
    found: List[str] = []
    pending = [name for _, node in _walk({"op": operation})
               for name in [_local_ref_name(node)] if name]
    while pending:
        name = pending.pop()
        if name in found or name not in schemas:
            continue
        found.append(name)
        pending.extend(
            n for _, node in _walk(schemas[name])
            for n in [_local_ref_name(node)] if n
        )
    return found


def _slice_for(document: Dict[str, Any], path: str, method: str) -> str:
    """The part of the assembled document one operation accounts for, as YAML.

    Cut from the document rather than taken from the fragment the Patcher
    returned: the Assembler merges, and a merge can change what ends up there —
    a schema name already taken keeps its first definition. What matters is
    what the document now says, which is also what makes the layout worth
    showing, since the text here is the text that was written out.

    Schemas reachable from the operation come along, so a $ref can be followed
    to what it names.
    """
    operation = ((document.get("paths") or {}).get(path) or {}).get(method)
    if operation is None:
        return "(this operation is not in the document)"

    schemas = (document.get("components") or {}).get("schemas") or {}
    wanted = _schemas_reached_by(document, path, method)

    part: Dict[str, Any] = {"paths": {path: {method: operation}}}
    if wanted:
        part["components"] = {"schemas": {n: schemas[n] for n in sorted(wanted)}}
    try:
        return yaml.dump(part, sort_keys=False, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        logger.warning(f"Reflector → could not serialise {method} {path}: {e}")
        return "(this operation could not be serialised)"


def _local_ref_name(node: Any) -> Optional[str]:
    """The schema a node's $ref names, when it points inside this document."""
    if isinstance(node, dict) and isinstance(node.get("$ref"), str):
        match = _LOCAL_REF.match(node["$ref"].strip().strip("'\""))
        if match:
            return match.group(1)
    return None


def _orphan_schemas_yaml(document: Dict[str, Any], covered: set) -> str:
    """Schemas no operation reached, so nothing is reviewed only by omission."""
    schemas = (document.get("components") or {}).get("schemas") or {}
    left = {n: s for n, s in schemas.items() if n not in covered}
    if not left:
        return ""
    try:
        return yaml.dump({"components": {"schemas": left}}, sort_keys=False,
                         allow_unicode=True, default_flow_style=False)
    except Exception:
        return ""


def _document_yaml(document: Dict[str, Any], limit: int = 24000) -> str:
    try:
        text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    except Exception as e:
        logger.warning(f"Reflector → could not serialise the document: {e}")
        return "(the document could not be serialised)"
    if len(text) > limit:
        return text[:limit] + f"\n… truncated at {limit} characters"
    return text


# ── Node ────────────────────────────────────────────────────────────────────

def reflector_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
    document = state.get("final_openapi") or {}
    if not (document.get("paths") or document.get("components")):
        logger.info("Reflector → nothing generated yet; skipping")
        return {"fragment_reflection": {"problems": [], "summary": ""}}

    structural = _structural_checks(document)

    if llm is None:
        from openapi_generator.config.llm_config import get_llm
        llm = get_llm()

    from openapi_generator.prompts.reflector_prompts import (
        reflector_document_prompt,
        reflector_parts_prompt,
    )
    from openapi_generator.schemas.operation import ReflectorReview

    rules = (state.get("rules_bank") or {}).get("rules") or []
    plan = state.get("operations_plan") or []
    document_yaml = _document_yaml(document)
    rules_block = _format_rules(rules, plan)

    if retriever is None:
        from openapi_generator.rag.retriever import get_relevant_chunks
        retriever = get_relevant_chunks
    spec_context, reference = document_rag_context(document, rules, plan, retriever)

    # ── Stage 2 — one pass per operation ────────────────────────────────
    # Reviewing the document a part at a time is what keeps each part next to
    # the rules that produced it. Read whole, the correspondence is lost and
    # sound parts start to look wrong against a standard nobody set.
    first: List[Dict[str, Any]] = []
    op_context = state.get("op_context") or {}
    chain = reflector_parts_prompt | llm.with_structured_output(
        ReflectorReview, method="function_calling"
    )

    covered: set = set()
    cleared: List[str] = []
    for op in plan:
        path, method = op.get("path") or "", (op.get("method") or "").lower()
        if not path or not method:
            continue
        part = _slice_for(document, path, method)
        covered.update(_schemas_reached_by(document, path, method))

        rule_ids = [rid for rid in (op.get("source_rule_ids") or []) if 0 <= rid < len(rules)]
        op_rules, _ = _build_rules_block(rules, rule_ids)
        # The context the Patcher retrieved to write this operation; judging it
        # against anything else invites disagreement over nothing.
        kept = op_context.get(f"{method} {path}") or {}

        try:
            review: ReflectorReview = chain.invoke({
                "operation": f"{method.upper()} {path}",
                "document_yaml": part,
                "rules_by_operation": op_rules,
                "rule_type_guide": RULE_TYPE_GUIDE,
                "structural_report": _format_problems(
                    [p for p in structural if path in p["where"] or not p["where"].startswith("paths")]
                ),
                "rag_context": kept.get("rag_context") or spec_context or "(no 3GPP context)",
                "openapi_reference": kept.get("openapi_reference") or reference
                or "(no OpenAPI reference)",
            })
            found = [issue.model_dump() for issue in review.issues]
            first.extend(found)
            if not found:
                cleared.append(f"{method.upper()} {path}")
            logger.info(
                f"Reflector → {method.upper()} {path}: "
                + (f"{len(found)} issue(s)" if found else "nothing to report")
            )
        except Exception as e:
            logger.error(f"Reflector → pass over {method} {path} failed: {e}", exc_info=True)

    orphans = _orphan_schemas_yaml(document, covered)
    if orphans:
        try:
            review = chain.invoke({
                "operation": "schemas no operation references",
                "document_yaml": orphans,
                "rules_by_operation": rules_block,
                "rule_type_guide": RULE_TYPE_GUIDE,
                "structural_report": _format_problems(
                    [p for p in structural if p["where"].startswith("components")]
                ),
                "rag_context": spec_context or "(no 3GPP context)",
                "openapi_reference": reference or "(no OpenAPI reference)",
            })
            first.extend(issue.model_dump() for issue in review.issues)
        except Exception as e:
            logger.error(f"Reflector → pass over unreferenced schemas failed: {e}", exc_info=True)

    logger.info(f"Reflector → first pass over {len(plan)} operation(s): {len(first)} issue(s)")

    # ── Stage 3 — the document as a whole ───────────────────────────────
    try:
        chain = reflector_document_prompt | llm.with_structured_output(
            ReflectorReview, method="function_calling"
        )
        final: ReflectorReview = chain.invoke({
            "document_yaml": document_yaml,
            "rules_by_operation": rules_block,
            "rule_type_guide": RULE_TYPE_GUIDE,
            "structural_report": _format_problems(structural),
            "first_pass_report": _format_problems(first),
            "cleared_parts": "\n".join(f"  - {p}" for p in cleared) or "(none)",
            "rag_context": spec_context or "(no 3GPP context)",
            "openapi_reference": reference or "(no OpenAPI reference)",
        })
    except Exception as e:
        logger.error(f"Reflector → second pass failed: {e}", exc_info=True)
        # Without the confirming pass, the unweighed findings are all there is.
        return {
            "fragment_reflection": {
                "problems": structural + first,
                "summary": "the confirming pass did not run; findings are unweighed",
                "confirmed": False,
            }
        }

    problems = [issue.model_dump() for issue in final.issues]
    errors = sum(1 for p in problems if p.get("severity") == "error")
    logger.info(
        f"Reflector → confirmed {len(problems)} problem(s) "
        f"({errors} error, {len(problems) - errors} warning) from "
        f"{len(structural)} structural + {len(first)} first-pass candidate(s)"
    )

    return {
        "fragment_reflection": {
            "problems": problems,
            "unapplied_rule_ids": final.unapplied_rule_ids,
            "ungrounded": final.ungrounded,
            "summary": final.summary,
            "confirmed": True,
        }
    }
