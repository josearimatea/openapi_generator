"""
Patcher — 1 LLM call per operation, RAG optional.

For the current TargetOperation in operations_plan[current_op_idx]:
  1. Collect the rules grounding it (rules_bank["rules"][i] for i in
     target_op.source_rule_ids).
  2. Pull the legacy fragment for this (path, method) when action is
     'update' or 'keep'.
  3. (Optional) Query the 3GPP RAG collection for supporting context.
  4. On retry (op_iteration_count > 0): feed the Validator's per-op
     feedback (validator_op_reflection + correction errors) back into
     the prompt as a CORRECTION TASK.
  5. Call the LLM with structured output (OperationFragment).

State reads:
    operations_plan, current_op_idx, op_iteration_count,
    rules_bank, legacy_openapi, final_openapi (for existing schemas),
    validation_errors, validator_op_reflection

State writes:
    current_fragment        — OperationFragment dict
    op_iteration_count      — incremented by 1
"""

import copy
import json
from typing import Any, Dict, List, Optional

import yaml

from openapi_generator.config import get_logger
from openapi_generator.prompts.patcher_prompts import patcher_prompt
from openapi_generator.schemas.operation import OperationFragment

logger = get_logger(__name__)


def _empty_fragment(target_op: Dict[str, Any]) -> Dict[str, Any]:
    """Skeleton returned when there is nothing to do (no LLM call)."""
    return {
        "path": target_op.get("path", ""),
        "method": target_op.get("method", ""),
        "paths": {},
        "components": {},
    }


def _build_rules_block(rules: List[Dict[str, Any]], indices: List[int]) -> str:
    """Render the applicable rules as readable lines for the prompt."""
    if not indices:
        return "(no rules attached to this operation)"
    lines: List[str] = []
    for i in indices:
        if not 0 <= i < len(rules):
            logger.warning(f"Patcher → rule index {i} out of range; skipping")
            continue
        r = rules[i]
        mapping = r.get("openapi_mapping") or {}
        lines.append(
            f"- [{i}] type={r.get('rule_type', '?')} "
            f"source={r.get('source_name', '?')!r}\n"
            f"      text       : {r.get('rule_text', '?')}\n"
            f"      maps to    : object={mapping.get('openapi_object', '?')} "
            f"field={mapping.get('openapi_field', '?')} "
            f"value={mapping.get('openapi_value', '?')!r}"
        )
    return "\n".join(lines) if lines else "(no resolvable rule indices)"


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
    applicable_rules = _build_rules_block(all_rules, source_rule_ids)
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
    openapi_reference = ""
    try:
        from openapi_generator.tools.rag_tools import search_openapi_reference
        openapi_reference = search_openapi_reference(rag_query)
        if openapi_reference:
            logger.info("Patcher → OpenAPI reference RAG returned chunks")
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
