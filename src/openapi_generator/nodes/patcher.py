"""
Patcher — up to 1 LLM call per operation, 2 RAGs (optional).

For the current TargetOperation in operations_plan[current_op_idx]:

  - Early-return for action in {'keep', 'discard'} → no LLM, no RAG. The
    legacy fragment already in final_openapi is kept (the Assembler will
    pop it when action='discard'). Returns an empty fragment.

  Otherwise (action in {'create', 'update'}):
    1. Collect the applicable rules from rules_bank["rules"] using the
       indices in target_op.source_rule_ids (assembled by the Planner),
       gating each one through _rule_verdict: rules the bank's Validator
       rejected are dropped or marked DISPUTED depending on what the
       Reflector thought of them.
    2. Pull the legacy fragment for this (path, method) when action is
       'update' (used as the baseline the LLM modifies).
    3. Query the 3GPP RAG collection for spec context (optional —
       degrades to "" if Qdrant or the collection is missing).
    4. Query the OpenAPI 3.0 reference RAG (search_openapi_reference)
       for authoritative spec excerpts (same graceful degradation).
    5. On retry (op_iteration_count > 0): inject the Validator's per-op
       feedback (validator_op_reflection + correction errors) as a
       CORRECTION TASK section in the prompt.
    6. Call the LLM with structured output (OperationFragment,
       method='function_calling' so open-ended paths/schemas are accepted).
    7. Re-anchor path/method on the output (the LLM occasionally renames
       them; the Patcher overwrites with the target values).

State reads:
    operations_plan, current_op_idx, op_iteration_count,
    rules_bank, legacy_openapi, final_openapi (for existing schemas),
    validation_errors, validator_op_reflection

State writes:
    current_fragment        — OperationFragment dict (empty for keep/discard)
    op_iteration_count      — incremented by 1

LLM/Retriever injection:
    llm        — passed via build_openapi_gen_graph(llm=...); falls back
                 to config.llm_config.get_llm() when None.
    retriever  — 3GPP spec RAG callable; falls back to
                 rag.retriever.get_relevant_chunks. OpenAPI reference RAG
                 is always tools.rag_tools.search_openapi_reference.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from openapi_generator.config import get_logger
from openapi_generator.config.settings import RULE_RESCUE_CONFIDENCE
from openapi_generator.prompts.patcher_prompts import patcher_prompt
from openapi_generator.schemas.operation import OperationFragment
from openapi_generator.schemas.rule_types import format_for_prompt

logger = get_logger(__name__)

# The full taxonomy, not just the types this operation happens to carry: the
# guidance has to hold for any MnS, and a bank that starts emitting a type the
# prompt never described would otherwise be handled blind.
RULE_TYPE_GUIDE = format_for_prompt()


def _empty_fragment(target_op: Dict[str, Any]) -> Dict[str, Any]:
    """Skeleton returned when there is nothing to do (no LLM call)."""
    return {
        "path": target_op.get("path", ""),
        "method": target_op.get("method", ""),
        "paths": {},
        "components": {},
    }


def _rule_verdict(rule: Dict[str, Any]) -> Tuple[str, str]:
    """Decide how one rules_bank rule may be used, returning (verdict, objection).

    A rule the bank's Validator approved is authoritative — nothing to decide.
    A rule it rejected was force-included by the bank's Builder at MAX_ITERATIONS,
    so the content is still there; the Reflector's own assessment breaks the tie:

      - the Reflector already wanted it gone (discard_suggestion), or flagged it
        while scoring it below RULE_RESCUE_CONFIDENCE          → 'drop'
      - the Reflector stands behind it (not flagged, or confident) → 'review'

    'drop' never reaches the prompt. 'review' does, annotated with the Validator's
    objection and the Reflector's reasoning, so the LLM arbitrates on the same
    evidence rather than treating a disputed rule as fact.

    The bank does not persist `error_type` on a force-included rule — its Builder
    folds it into `validation_notes` as "Force-included at max attempts.
    Failed: <instruction>". `reflection_confidence` may be null (newer extractor
    models leave it unset); absence means "not scored", not zero, so it never on
    its own condemns a rule.
    """
    if rule.get("validation_passed", True):
        return "ok", ""

    notes = (rule.get("validation_notes") or "").strip()
    objection = notes.split("Failed:", 1)[1].strip() if "Failed:" in notes else notes
    confidence = rule.get("reflection_confidence")
    scored_low = (
        isinstance(confidence, (int, float)) and confidence < RULE_RESCUE_CONFIDENCE
    )

    if rule.get("discard_suggestion") or (rule.get("reflection_flagged") and scored_low):
        return "drop", objection
    return "review", objection


def _build_rules_block(
    rules: List[Dict[str, Any]],
    indices: List[int],
) -> Tuple[str, Dict[str, List[int]]]:
    """Render the applicable rules for the prompt, gated by their bank verdict.

    Returns (block, buckets); buckets maps verdict → rule indices so the caller
    can log what was used and what was withheld.
    """
    buckets: Dict[str, List[int]] = {"ok": [], "review": [], "drop": []}
    if not indices:
        return "(no rules attached to this operation)", buckets

    lines: List[str] = []
    for i in indices:
        if not 0 <= i < len(rules):
            logger.warning(f"Patcher → rule index {i} out of range; skipping")
            continue
        r = rules[i]
        verdict, objection = _rule_verdict(r)
        buckets[verdict].append(i)
        if verdict == "drop":
            continue

        mapping = r.get("openapi_mapping") or {}
        confidence = r.get("reflection_confidence")
        entry = [
            f"- [{i}] type={r.get('rule_type', '?')} "
            f"source={r.get('source_name', '?')!r}",
            f"      status     : {'VALIDATED' if verdict == 'ok' else 'DISPUTED'}"
            + (f" (reflector confidence={confidence:.2f})"
               if isinstance(confidence, (int, float)) else ""),
            f"      text       : {r.get('rule_text', '?')}",
            f"      maps to    : object={mapping.get('openapi_object', '?')} "
            f"field={mapping.get('openapi_field', '?')} "
            f"value={mapping.get('openapi_value', '?')!r}",
        ]
        if verdict == "review":
            reasoning = " ".join((r.get("reflection_reasoning") or "").split())
            entry.append(f"      objection  : {objection}")
            if reasoning:
                entry.append(f"      defence    : {reasoning[:600]}")
            entry.append(
                "      -> Weigh the objection against the defence and the spec "
                "context. Apply this rule only if it holds up; otherwise omit it."
            )
        lines.append("\n".join(entry))

    return ("\n".join(lines) if lines else "(no usable rule for this operation)"), buckets


def _legacy_fragment_for(
    legacy_openapi: Optional[Dict[str, Any]],
    path: str,
    method: str,
) -> str:
    """Pretty-print the legacy fragment for (path, method) as YAML.

    Returns "" when no legacy is provided or when the operation is absent.
    """
    if not legacy_openapi:
        return ""
    paths = legacy_openapi.get("paths") or {}
    op = (paths.get(path) or {}).get(method)
    if not op:
        return ""
    snippet = {path: {method: op}}
    try:
        return yaml.safe_dump(snippet, sort_keys=False, allow_unicode=True).strip()
    except Exception as e:
        logger.warning(f"Patcher → could not serialize legacy fragment: {e}")
        return json.dumps(snippet, indent=2, ensure_ascii=False)


def _existing_schemas_summary(final_openapi: Optional[Dict[str, Any]]) -> str:
    schemas = ((final_openapi or {}).get("components") or {}).get("schemas") or {}
    if not schemas:
        return "(none yet — all schemas this operation needs must be defined here)"
    names = sorted(schemas.keys())
    return ", ".join(names)


def _build_correction_task(
    validation_errors: List[Dict[str, Any]],
    validator_op_reflection: Dict[str, Any],
) -> str:
    """Render the Validator's per-op feedback for a retry attempt."""
    if not validation_errors and not validator_op_reflection:
        return ""

    lines = [
        "CORRECTION TASK — this is a retry for the same operation.",
        "Apply the changes listed below to your previous fragment. Do NOT",
        "discard parts that were already correct; modify only what is flagged.",
        "",
    ]

    corrections = [e for e in validation_errors if e.get("error_type") == "correction"]
    if corrections:
        lines.append("ITEMS TO CORRECT:")
        for i, err in enumerate(corrections, start=1):
            lines.append(
                f"  {i}. stage={err.get('stage', '?')} ref={err.get('ref', '?')}\n"
                f"     instruction: {err.get('instruction', '?')}"
            )
        lines.append("")

    summary = validator_op_reflection.get("summary") if validator_op_reflection else ""
    if summary:
        lines.append(f"VALIDATOR SUMMARY:\n  {summary}")
    missing_rules = (validator_op_reflection or {}).get("missing_rules") or []
    if missing_rules:
        lines.append("RULES STILL MISSING:")
        for m in missing_rules:
            lines.append(f"  - {m}")
    structural = (validator_op_reflection or {}).get("structural_issues") or []
    if structural:
        lines.append("STRUCTURAL ISSUES:")
        for s in structural:
            lines.append(f"  - {s}")
    semantic = (validator_op_reflection or {}).get("semantic_priorities") or []
    if semantic:
        lines.append("SEMANTIC PRIORITIES:")
        for s in semantic:
            lines.append(f"  - {s}")

    return "\n".join(lines)


def _strip_ref_siblings(node: Any, _at: str = "") -> List[str]:
    """Drop keys sitting beside a `$ref`, in place. Returns the paths cleaned.

    OpenAPI 3.0 defines `$ref` as exclusive: a parser resolves the reference and
    ignores every sibling key, so `description`/`type`/`example` written next to
    one are silently lost. The Patcher prompt forbids them, but the guarantee is
    cheap to enforce here and keeps an LLM slip from reaching the document.
    """
    cleaned: List[str] = []
    if isinstance(node, dict):
        if "$ref" in node and len(node) > 1:
            for key in [k for k in node if k != "$ref"]:
                node.pop(key)
                cleaned.append(f"{_at}.{key}" if _at else key)
        for key, value in node.items():
            cleaned.extend(_strip_ref_siblings(value, f"{_at}.{key}" if _at else str(key)))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            cleaned.extend(_strip_ref_siblings(value, f"{_at}[{i}]"))
    return cleaned


# Signals in a rule → the OpenAPI 3.0 section that governs what it produces.
# Keys are the reference document's own headings, which is what the collection
# is indexed on.
_CONSTRUCT_BY_SIGNAL = {
    "Reference Object $ref": lambda field, value, rtype: "$ref" in value,
    "Schema Object composition allOf oneOf anyOf":
        lambda field, value, rtype: field in ("allOf", "oneOf", "anyOf"),
    "Responses Object status codes content":
        lambda field, value, rtype: rtype == "response",
    "Request Body Object content":
        lambda field, value, rtype: rtype == "request_body",
    "Parameter Object in path query":
        lambda field, value, rtype: rtype in ("path_parameter", "query_parameter"),
}


def _openapi_reference_query(rules: List[Dict[str, Any]], indices: List[int]) -> str:
    """Name the OpenAPI constructs this fragment will use, for the reference RAG.

    The two RAGs need different questions. The 3GPP collection is asked in the
    specification's own vocabulary ("POST /notificationSink —
    notifyThresholdCrossing"). The OpenAPI-reference collection holds the
    OpenAPI 3.0 document, where those words never occur — asking it the same
    question returns whatever sits nearby rather than the sections that govern
    what we are about to write. The rules already say which constructs the
    fragment will contain, so we ask for those by name.
    """
    wanted = []
    for i in indices:
        if not 0 <= i < len(rules):
            continue
        mapping = rules[i].get("openapi_mapping") or {}
        args = (
            str(mapping.get("openapi_field") or ""),
            str(mapping.get("openapi_value") or ""),
            rules[i].get("rule_type") or "",
        )
        wanted += [name for name, matches in _CONSTRUCT_BY_SIGNAL.items()
                   if matches(*args) and name not in wanted]
    return " — ".join(wanted) if wanted else "Schema Object Operation Object"


def _decide_servers(
    llm,
    retriever,
    spec_text: str,
    document_paths: List[str],
) -> List[Dict[str, Any]]:
    """Work out the document's `servers`, reading the specification.

    The generator owns this block: it says where the service is hosted, which
    is a property of the document rather than a rule extracted from a clause.

    A specification may legitimately declare no server — a notification sink
    addressed through a subscription has none the producer could name — and in
    that case a marked placeholder goes in, so the document still tells a
    client what to call and the gap stays visible instead of being filled with
    a sibling service's prefix.
    """
    from openapi_generator.prompts.patcher_prompts import patcher_servers_prompt
    from openapi_generator.schemas.operation import PatcherServerDecision
    from openapi_generator.utils.servers import placeholder_servers, uri_clauses

    rag_context = ""
    if retriever is not None and document_paths:
        try:
            chunks = retriever(
                f"resource URI structure server root {document_paths[0]}", k=3
            )
            rag_context = "\n\n---\n\n".join(chunks or [])
        except Exception as e:
            logger.warning(f"Patcher → servers RAG failed: {e}")

    try:
        chain = patcher_servers_prompt | llm.with_structured_output(
            PatcherServerDecision, method="function_calling"
        )
        decision: PatcherServerDecision = chain.invoke({
            "document_paths": ", ".join(document_paths) or "(none yet)",
            "uri_clauses": uri_clauses(spec_text, document_paths),
            "rag_context": rag_context or "(no 3GPP RAG context)",
        })
    except Exception as e:
        logger.error(f"Patcher → servers pass failed: {e}", exc_info=True)
        return [placeholder_servers(
            f"The servers pass could not run ({type(e).__name__}), so the "
            "specification was never consulted for this block."
        )]

    if not decision.url:
        logger.info(f"Patcher → no server in the specification: {decision.rationale[:120]}")
        return [placeholder_servers(
            decision.rationale
            or "The specification states no server for this service."
        )]

    server: Dict[str, Any] = {"url": decision.url}
    if decision.description:
        server["description"] = decision.description
    if decision.variables:
        # description before default, the order the published specs use.
        server["variables"] = {
            v.name: {
                key: value
                for key, value in (("description", v.description), ("default", v.default))
                if value or key == "default"
            }
            for v in decision.variables
        }
    logger.info(f"Patcher → servers: {decision.url}")
    return [server]


def _rag_query_for(target_op: Dict[str, Any], rules: List[Dict[str, Any]]) -> str:
    """Compose a short query for the RAG retriever from the op + first rules."""
    parts: List[str] = []
    method = (target_op.get("method") or "").upper()
    path = target_op.get("path") or ""
    if method and path:
        parts.append(f"{method} {path}")
    # Add the source_name of the first 2 rules for extra anchoring.
    seen: set = set()
    for r in rules[:6]:
        name = r.get("source_name")
        if name and name not in seen:
            parts.append(name)
            seen.add(name)
        if len(seen) >= 2:
            break
    return " — ".join(parts) if parts else (target_op.get("path") or "")


def _apply_corrections(state: dict, llm, retriever) -> Dict[str, Any]:
    """Apply the Validator's fixes to the assembled document.

    One call for all of them. The fixes touch places spread across the document
    and some depend on each other — a schema added and the $ref pointing at it —
    so applying them one at a time would have each call rewrite what the last
    one just did.

    The document is edited rather than generated again: everything no fix names
    comes back untouched, which is the point. Regenerating would put work
    already judged correct back at risk.
    """
    from openapi_generator.nodes.reflector import document_rag_context
    from openapi_generator.prompts.patcher_prompts import patcher_correction_prompt
    from openapi_generator.schemas.operation import PatcherCorrectedDocument

    document = state.get("final_openapi") or {}
    fixes = state.get("validation_errors") or []
    iteration = state.get("op_iteration_count", 0)
    rules = (state.get("rules_bank") or {}).get("rules") or []
    plan = state.get("operations_plan") or []

    logger.info(f"Patcher → applying {len(fixes)} fix(es), round {iteration + 1}")

    rule_ids = sorted({rid for op in plan for rid in (op.get("source_rule_ids") or [])})
    rules_block, _ = _build_rules_block(rules, rule_ids)
    _, reference = document_rag_context(document, rules, plan, retriever=None)

    fixes_block = "\n".join(
        f"  - {f.get('action', 'change').upper()} at {f.get('ref', '?')}\n"
        f"      {f.get('instruction', '')}"
        + (f"\n      rules: {f['rule_ids']}" if f.get("rule_ids") else "")
        for f in fixes
    )

    try:
        chain = patcher_correction_prompt | llm.with_structured_output(
            PatcherCorrectedDocument, method="function_calling"
        )
        corrected: PatcherCorrectedDocument = chain.invoke({
            "document_yaml": yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
            "fixes": fixes_block,
            "applicable_rules": rules_block,
            "openapi_reference": reference or "(no OpenAPI reference)",
        })
    except Exception as e:
        logger.error(f"Patcher → correction pass failed: {e}", exc_info=True)
        # Leave the document as it was; the round is spent either way, and the
        # budget in graph.conditions stops the loop.
        return {"op_iteration_count": iteration + 1, "validation_errors": []}

    # Take what came back key by key rather than swapping the blocks whole. The
    # correction is asked for the entire document, but a model that returns
    # only the part it touched would otherwise delete everything it left out —
    # and a schema silently dropped here is far worse than a fix not applied.
    # A removal therefore has to be asked for: `remove` fixes name what goes.
    updated = copy.deepcopy(document)
    removals = {
        f.get("ref", "") for f in fixes if f.get("action") == "remove"
    }

    def merge(into: Dict[str, Any], came_back: Dict[str, Any], at: str) -> None:
        for key, value in came_back.items():
            here = f"{at}.{key}" if at else key
            if isinstance(value, dict) and isinstance(into.get(key), dict):
                merge(into[key], value, here)
            else:
                into[key] = value
        for key in list(into):
            if key in came_back:
                continue
            here = f"{at}.{key}" if at else key
            if any(r.startswith(here) for r in removals):
                into.pop(key)
                logger.info(f"Patcher → removed {here}")

    # `applied` and `skipped` are the model's report on its own work, and the
    # structured output invites it to nest them inside the blocks it returns
    # rather than beside them. They are not OpenAPI and must not reach the
    # document, so they are stripped wherever they turn up.
    def content_only(block: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in block.items() if k not in ("applied", "skipped")}

    if corrected.paths:
        merge(updated.setdefault("paths", {}), content_only(corrected.paths), "paths")
    if corrected.components:
        merge(
            updated.setdefault("components", {}),
            content_only(corrected.components),
            "components",
        )

    logger.info(
        f"Patcher → applied {len(corrected.applied)} fix(es)"
        + (f", skipped {len(corrected.skipped)}" if corrected.skipped else "")
    )
    for skipped in corrected.skipped:
        logger.warning(f"Patcher → fix not applied: {skipped}")

    target = state.get("openapi_target_path") or ""
    out: Dict[str, Any] = {
        "final_openapi": updated,
        "op_iteration_count": iteration + 1,
        # Cleared so the next review starts from what the document now says.
        "validation_errors": [],
        "fragment_reflection": {},
    }
    if target:
        try:
            from openapi_generator.nodes.assembler import _write_final_yaml
            out["final_output_path"] = _write_final_yaml(updated, target)
        except Exception as e:
            logger.error(f"Patcher → could not rewrite the YAML: {e}", exc_info=True)
    return out


def patcher_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
    # Two modes. With fixes pending, the document is corrected as a whole;
    # otherwise the next operation in the plan is generated.
    if state.get("validation_errors"):
        if llm is None:
            from openapi_generator.config.llm_config import get_llm
            llm = get_llm()
        return _apply_corrections(state, llm, retriever)

    plan = state.get("operations_plan") or []
    idx = state.get("current_op_idx", 0)
    iteration = state.get("op_iteration_count", 0)

    if not (0 <= idx < len(plan)):
        logger.error(
            f"Patcher → current_op_idx={idx} out of bounds (plan size={len(plan)}); "
            "returning empty fragment"
        )
        return {
            "current_fragment": _empty_fragment({}),
            "op_iteration_count": iteration + 1,
        }

    target_op = plan[idx]
    path = target_op.get("path", "")
    method = target_op.get("method", "")
    action = target_op.get("action", "create")
    source_rule_ids = target_op.get("source_rule_ids") or []
    op_label = f"{method.upper()} {path}"

    logger.info(
        f"Patcher → op {idx + 1}/{len(plan)} ({op_label}) "
        f"action={action} attempt={iteration + 1}"
    )

    # Early-return: 'keep' is a no-op (legacy already in final_openapi via Loader),
    # 'discard' is handled by the Assembler. In both cases no LLM call is needed.
    if action in ("keep", "discard"):
        logger.info(f"Patcher → skipping LLM for action={action} on {op_label}")
        return {
            "current_fragment": _empty_fragment(target_op),
            "op_iteration_count": iteration + 1,
        }

    rules_bank = state.get("rules_bank") or {}
    all_rules: List[Dict[str, Any]] = rules_bank.get("rules") or []
    legacy_openapi = state.get("legacy_openapi")
    final_openapi = state.get("final_openapi")
    validation_errors = state.get("validation_errors") or []
    validator_op_reflection = state.get("validator_op_reflection") or {}

    # ── LLM (lazy default) ────────────────────────────────────────
    if llm is None:
        from openapi_generator.config.llm_config import get_llm
        llm = get_llm()

    # ── Prompt inputs ─────────────────────────────────────────────
    target_op_summary = (
        f"path={path}\nmethod={method}\naction={action}\n"
        f"priority={target_op.get('priority', 'medium')}\n"
        f"rationale={target_op.get('rationale', '')!r}"
    )
    applicable_rules, rule_buckets = _build_rules_block(all_rules, source_rule_ids)
    logger.info(
        f"Patcher → rules for {op_label}: {len(rule_buckets['ok'])} validated, "
        f"{len(rule_buckets['review'])} disputed (kept for review), "
        f"{len(rule_buckets['drop'])} dropped"
    )
    if rule_buckets["drop"]:
        logger.info(f"Patcher → dropped rule ids {rule_buckets['drop']}")
    legacy_fragment = _legacy_fragment_for(legacy_openapi, path, method)
    existing_schemas = _existing_schemas_summary(final_openapi)

    # ── RAG #1 — 3GPP spec context (optional, degrades to "") ─────
    rag_context = ""
    if retriever is None:
        from openapi_generator.rag.retriever import get_relevant_chunks
        retriever = get_relevant_chunks
    rag_query = _rag_query_for(
        target_op,
        [all_rules[i] for i in source_rule_ids if 0 <= i < len(all_rules)],
    )
    try:
        chunks = retriever(rag_query, k=4)
        if chunks:
            rag_context = "\n\n---\n\n".join(chunks)
            logger.info(f"Patcher → 3GPP RAG returned {len(chunks)} chunk(s)")
    except Exception as e:
        logger.warning(f"Patcher → 3GPP RAG call failed: {e}")

    # ── RAG #2 — OpenAPI 3.0 reference (optional, degrades to "") ─
    # Asked by construct name, not in 3GPP vocabulary — see
    # _openapi_reference_query.
    openapi_reference = ""
    reference_query = _openapi_reference_query(all_rules, source_rule_ids)
    try:
        from openapi_generator.tools.rag_tools import search_openapi_reference
        openapi_reference = search_openapi_reference(reference_query)
        if openapi_reference:
            logger.info(
                f"Patcher → OpenAPI reference RAG returned chunks "
                f"for query={reference_query!r}"
            )
    except Exception as e:
        logger.warning(f"Patcher → OpenAPI reference RAG call failed: {e}")

    correction_task = _build_correction_task(validation_errors, validator_op_reflection)
    if correction_task:
        logger.info(
            f"Patcher → retry: {len([e for e in validation_errors if e.get('error_type') == 'correction'])} "
            "correction(s) fed back to the LLM"
        )

    # ── LLM call ──────────────────────────────────────────────────
    # OpenAI's strict structured-output mode rejects open-ended `Dict[str, Any]`
    # fields (it requires `additionalProperties: false`). Our fragment must
    # accept arbitrary path strings under `paths` and arbitrary schema names
    # under `components.schemas`, so we use function-calling mode instead.
    structured_llm = llm.with_structured_output(
        OperationFragment, method="function_calling"
    )
    chain = patcher_prompt | structured_llm
    try:
        fragment: OperationFragment = chain.invoke({
            "target_op_summary": target_op_summary,
            "applicable_rules": applicable_rules,
            "legacy_fragment": legacy_fragment or "(no legacy fragment — building from scratch)",
            "existing_schemas": existing_schemas,
            "rule_type_guide": RULE_TYPE_GUIDE,
            "rag_context": rag_context or "(3GPP RAG unavailable for this run)",
            "openapi_reference": openapi_reference or "(OpenAPI reference RAG unavailable for this run)",
            "correction_task": correction_task,
        })
    except Exception as e:
        logger.error(f"Patcher → LLM call failed: {e}", exc_info=True)
        return {
            "current_fragment": _empty_fragment(target_op),
            "op_iteration_count": iteration + 1,
        }

    fragment_dict = fragment.model_dump()

    # Force consistency: the LLM might rename path/method — re-anchor.
    if fragment_dict.get("path") != path:
        logger.warning(
            f"Patcher → LLM emitted path={fragment_dict.get('path')!r}, "
            f"overwriting with target {path!r}"
        )
        fragment_dict["path"] = path
    if fragment_dict.get("method") != method:
        logger.warning(
            f"Patcher → LLM emitted method={fragment_dict.get('method')!r}, "
            f"overwriting with target {method!r}"
        )
        fragment_dict["method"] = method

    paths_block = fragment_dict.get("paths") or {}
    schemas_block = (fragment_dict.get("components") or {}).get("schemas") or {}
    logger.info(
        f"Patcher → produced fragment for {op_label}: "
        f"{len(paths_block)} path block(s), {len(schemas_block)} new schema(s)"
    )

    out: Dict[str, Any] = {
        "current_fragment": fragment_dict,
        "op_iteration_count": iteration + 1,
        # Keep what was retrieved to write this operation. The Reflector reviews
        # it later and needs the same grounding: judged against other passages,
        # a sound operation can be made to look wrong.
        "op_context": {
            **(state.get("op_context") or {}),
            f"{method} {path}": {
                "rag_context": rag_context,
                "openapi_reference": openapi_reference,
            },
        },
    }

    # `servers` belongs to the document, not to any one operation, so it is
    # settled once — on the first operation that produces paths, which are what
    # identify the service when a specification defines several. It goes
    # straight into final_openapi rather than through the fragment, which the
    # Assembler merges under `paths` and `components`.
    if final_openapi is not None and not final_openapi.get("servers"):
        document_paths = sorted(
            set(final_openapi.get("paths") or {}) | set(paths_block)
        )
        if document_paths:
            spec_text = ""
            spec_path = state.get("spec_doc_path") or ""
            if spec_path:
                try:
                    spec_text = Path(spec_path).read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(f"Patcher → could not read the spec for servers: {e}")
            updated = copy.deepcopy(final_openapi)
            updated["servers"] = _decide_servers(
                llm, retriever, spec_text, document_paths
            )
            out["final_openapi"] = updated

    return out
