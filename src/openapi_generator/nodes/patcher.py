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


def patcher_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
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

    return {
        "current_fragment": fragment_dict,
        "op_iteration_count": iteration + 1,
    }
