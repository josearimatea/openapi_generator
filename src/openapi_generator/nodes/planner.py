"""
Planner — LLM (1 call), no RAG.

Reads the rules_bank (240ish rules, each one mapping to a path/parameter/
schema property/etc.) and the optional legacy OpenAPI, and produces an
ordered list of TargetOperation — the unit of work for the per-operation
loop downstream.

Field ownership: writes `operations_plan` (List[TargetOperation] serialized
to dicts) and `current_op_idx=0`.

LLM injection:
    llm — passed via build_openapi_gen_graph(llm=...); if None falls back to
          the lazy default in openapi_generator.config.llm_config.get_llm().
"""

from typing import Any, Dict, List, Optional

from openapi_generator.config import get_logger
from openapi_generator.prompts.planner_prompts import planner_prompt
from openapi_generator.schemas.operation import ExtractionPlan

logger = get_logger(__name__)

def _rule_to_row(idx: int, rule: Dict[str, Any]) -> str:
    """One row of the rules table sent to the LLM.

    Only `idx` is a Planner-meaningful integer — it is the value the LLM
    must use in `source_rule_ids`. Other ids (e.g. rules_bank section_id)
    are intentionally omitted so the LLM has a single numeric column to
    reference and cannot confuse them with anything else.
    """
    mapping = rule.get("openapi_mapping") or {}
    obj = mapping.get("openapi_object", "")
    field = mapping.get("openapi_field", "")
    obj_with_field = f"{obj}.{field}" if field else obj
    # Send the full rule_text — entries are short and the model needs the
    # full statement to attach the rule to the right operation.
    text = (rule.get("rule_text") or "").replace("\n", " ")
    return f"{idx} | {rule.get('rule_type', '?')} | {obj_with_field} | {text}"


def _build_rules_summary(rules: List[Dict[str, Any]]) -> str:
    if not rules:
        return "(no rules provided)"
    n = len(rules)
    header = (
        f"index | rule_type | openapi_object | rule_text\n"
        f"(valid index range: 0..{n - 1} inclusive — use ONLY these values "
        "in source_rule_ids; never invent or reuse numbers seen elsewhere)"
    )
    rows = [_rule_to_row(i, r) for i, r in enumerate(rules)]
    return header + "\n" + "\n".join(rows)


def _build_legacy_summary(legacy: Optional[Dict[str, Any]]) -> str:
    """Compact (path, method) listing of the legacy OpenAPI."""
    if not legacy:
        return "(no legacy OpenAPI provided — every operation must be created from scratch)"
    paths = legacy.get("paths") or {}
    if not paths:
        return "(legacy OpenAPI is empty — every operation must be created from scratch)"
    lines: List[str] = []
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method.lower() not in {"get", "put", "post", "delete", "patch", "head", "options"}:
                continue
            summary = ""
            if isinstance(op, dict):
                summary = op.get("summary") or op.get("operationId") or ""
            lines.append(f"  - {method.lower()} {path}" + (f"  ({summary})" if summary else ""))
    if not lines:
        return "(legacy OpenAPI has no HTTP operations)"
    return "\n".join(lines)


def planner_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
    # retriever: unused (Planner reasons over rules_bank, no RAG).
    logger.info("Planner Node started.")

    rules_bank = state.get("rules_bank") or {}
    rules: List[Dict[str, Any]] = rules_bank.get("rules") or []
    legacy = state.get("legacy_openapi")

    if not rules:
        logger.warning("Planner → rules_bank is empty; returning empty operations_plan")
        return {"operations_plan": [], "current_op_idx": 0}

    if llm is None:
        from openapi_generator.config.llm_config import get_llm
        llm = get_llm()

    rules_summary = _build_rules_summary(rules)
    legacy_summary = _build_legacy_summary(legacy)
    document_summary = (
        ((rules_bank.get("metadata") or {}).get("source_document") or "").split("/")[-1]
        or "(unknown source)"
    )

    logger.debug(
        f"Planner inputs — rules: {len(rules)}, "
        f"legacy_paths: {len((legacy or {}).get('paths') or {})}"
    )

    structured_llm = llm.with_structured_output(ExtractionPlan)
    chain = planner_prompt | structured_llm

    plan: ExtractionPlan = chain.invoke({
        "rules_summary": rules_summary,
        "legacy_summary": legacy_summary,
        "document_summary": document_summary,
    })

    ops = [op.model_dump() for op in plan.operations_to_generate]
    by_action = {a: sum(1 for o in ops if o["action"] == a) for a in ("create", "update", "keep")}
    covered = {idx for o in ops for idx in o["source_rule_ids"]}

    logger.info(
        f"Planner Node complete — {len(ops)} operation(s): "
        f"{by_action['create']} create / {by_action['update']} update / {by_action['keep']} keep; "
        f"rules covered: {len(covered)}/{len(rules)}"
    )
    if plan.document_summary:
        logger.debug(f"Document summary: {plan.document_summary}")

    return {
        "operations_plan": ops,
        "current_op_idx": 0,
    }
