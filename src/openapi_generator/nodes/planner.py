"""
Planner — two passes built around three OpenAPI destinations.

Every rule in rules_bank is classified by rule_type into one of four
group keys (see _rule_group_key; the taxonomy lives in
schemas.rule_types.RULE_TYPES):
    op:<path>:<method>      — path_operation / request_body / response /
                              callback (anchored on the operation that
                              registers it)
    path:<path>             — path_parameter / query_parameter
    schema:<SchemaName>     — schema_property
    security:<SchemeName>   — security_scheme

Pass 1 — Polish the legacy
  For every (path, method) already present in legacy_openapi:
    1. Filter candidate rules whose group key touches this op (op-key on
       matching path+method; path-key on matching path; schema-key when the
       legacy fragment already $refs that schema).
    2. Pull RAG context: 3GPP spec + OpenAPI 3.0 reference.
    3. Call the LLM with pass1_prompt → PlannerPass1Verdict
       (action ∈ {keep, update, discard}, source_rule_ids).

Pass 2 — Three phases over the residual rules
  Phase A (op groups)     — 1 LLM call per (path, method) group via
                            pass2_prompt → PlannerPass2Op decides new or
                            attach to an existing op in the plan.
  Phase B (path groups)   — deterministic: append rule indices to every
                            operation already in the plan that shares the
                            path. No LLM call.
  Phase C (schema groups) — 1 LLM call per schema via pass2_schema_prompt
                            → PlannerSchemaAttachment lists which ops use
                            the schema; rule indices are appended there.

Anything left unconsumed at the end is reported in ExtractionPlan.probable_gaps.

State reads : rules_bank, legacy_openapi, parsed_spec_sections (unused, kept
              for state symmetry).
State writes: operations_plan (List[TargetOperation] as dicts),
              current_op_idx=0,
              extraction_plan (ExtractionPlan dump for debug).

LLM/Retriever injection:
    llm        — passed via build_openapi_gen_graph(llm=...); falls back to
                 config.llm_config.get_llm() when None.
    retriever  — callable used for 3GPP spec RAG (defaults to
                 rag.retriever.get_relevant_chunks). OpenAPI reference RAG
                 is always tools.rag_tools.search_openapi_reference.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from openapi_generator.config import get_logger
from openapi_generator.prompts.planner_prompts import (
    pass1_prompt,
    pass2_prompt,
    pass2_schema_prompt,
)
from openapi_generator.schemas.operation import (
    ExtractionPlan,
    PlannerPass1Verdict,
    PlannerPass2Op,
    PlannerSchemaAttachment,
    TargetOperation,
)

logger = get_logger(__name__)

_HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


# ── Deterministic helpers ────────────────────────────────────────────────────

def _list_legacy_ops(legacy: Optional[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """All (path, method) pairs declared in the legacy OpenAPI."""
    if not legacy:
        return []
    paths = legacy.get("paths") or {}
    out: List[Tuple[str, str]] = []
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method in ops:
            if method.lower() in _HTTP_METHODS:
                out.append((path, method.lower()))
    return out


def _legacy_fragment_yaml(legacy: Dict[str, Any], path: str, method: str) -> str:
    import yaml
    op = ((legacy.get("paths") or {}).get(path) or {}).get(method)
    if not op:
        return "(legacy fragment missing — should not happen)"
    snippet = {path: {method: op}}
    try:
        return yaml.safe_dump(snippet, sort_keys=False, allow_unicode=True).strip()
    except Exception:
        import json
        return json.dumps(snippet, indent=2, ensure_ascii=False)


def _rule_group_key(rule: Dict[str, Any]) -> Optional[str]:
    """
    Route one rule to its destination in the final OpenAPI document.

    This is addressing, not planning: `rule_type` says which OpenAPI construct
    the rule describes, and `openapi_mapping` says where it lands. Both were
    decided by the rules bank, which did the semantic work of reading the spec;
    here they are only read back. The judgement calls belong to the LLM passes
    that follow (Pass 1 keep/update/discard, Phase A new-or-attach, Phase C
    schema attachment), and those operate on the groups this function forms.

    The eight rule types the rules bank emits (openapi_rulesbank RawRule), and
    where each belongs:

      path_operation   IS operation name → paths.<path>, method in openapi_field
      request_body     IS operation name → paths.<path>.<method>.requestBody
      response         IS operation name → paths.<path>.<method>.responses
      path_parameter   param name        → paths.<path>, applies to every method
      query_parameter  param name        → paths.<path>, applies to every method
      schema_property  NRM attribute     → components.schemas.<Name>
      callback         notification name → callbacks of the operation that
                                           registers it
      security_scheme  scheme name       → components.securitySchemes.<Name>

    Key shapes returned:
      - 'op:<path>:<method>'     → paths.<path>.<method>
      - 'path:<path>'            → every method under that path
      - 'schema:<SchemaName>'    → components.schemas.<Name>
      - 'security:<SchemeName>'  → components.securitySchemes.<Name>

    Returns None when the rule cannot be placed, which drops it from the plan
    entirely — it never reaches the Patcher, so it is never weighed by any LLM.
    A rule type missing from this function is therefore silently lost, not
    judged badly; keep it in step with the rules bank taxonomy above.
    """
    rule_type = rule.get("rule_type")
    mapping = rule.get("openapi_mapping") or {}
    obj = mapping.get("openapi_object") or ""
    field = mapping.get("openapi_field") or ""

    # ── schema-only rules ────────────────────────────────────────────────────
    if rule_type == "schema_property":
        # Examples seen: 'components/schemas/<Name>' or 'components.schemas.<Name>'
        m = re.match(r"^components[/.]schemas[/.]([A-Za-z0-9_]+)$", obj)
        if m:
            return f"schema:{m.group(1)}"
        return None

    # ── security schemes ─────────────────────────────────────────────────────
    if rule_type == "security_scheme":
        m = re.match(r"^components[/.]securitySchemes[/.]([A-Za-z0-9_]+)$", obj)
        return f"security:{m.group(1)}" if m else None

    # ── path-anchored rules ──────────────────────────────────────────────────
    if not obj.startswith("paths."):
        return None
    rest = obj[len("paths."):]

    # ── callbacks ────────────────────────────────────────────────────────────
    # A Callback Object lives inside the operation that registers it, so a rule
    # about one belongs to that operation — not to a path of its own. Cut
    # everything from '.callbacks.' onwards and anchor on what precedes it.
    #
    # Two shapes occur. Some rules carry rule_type 'callback'; others describe
    # the callback's own operation with the ordinary types (path_operation,
    # request_body, response) and put the whole callback chain in
    # openapi_object. Both are handled here, before the per-type branches
    # below, because otherwise the dotted chain reads as a URL and yields paths
    # such as '/{className}={id}.callbacks.notifyMOICreation.{request.body#/…}'.
    if ".callbacks." in rest or "/callbacks/" in rest:
        owner = re.split(r"[./]callbacks[./]", rest, maxsplit=1)[0]
        # The registering method may close the owner chain
        # ('/x.post.callbacks.…'); when it does, it is the method we want. The
        # method sitting AFTER '.callbacks.' belongs to the callback itself and
        # must not be mistaken for it.
        tail = re.search(
            r"^(.+)[./](get|put|post|delete|patch|head|options)$",
            owner,
            flags=re.IGNORECASE,
        )
        if tail:
            return f"op:{tail.group(1)}:{tail.group(2).lower()}"
        # Otherwise the owner is a bare path: take the method from
        # openapi_field when it names one, else group by path so Phase B
        # spreads the rule over every method of that path.
        method = field.lower()
        return f"op:{owner}:{method}" if method in _HTTP_METHODS else f"path:{owner}"

    # path_operation: openapi_object is paths.<path>; method is in openapi_field.
    if rule_type == "path_operation":
        method = field.lower()
        if method in _HTTP_METHODS:
            return f"op:{rest}:{method}"
        return None

    # request_body / response: openapi_object is paths.<path>.<method>.<rest>.
    if rule_type in ("request_body", "response"):
        # Split off everything from .<method>.<rest> onwards. Use a regex that
        # finds the last HTTP method token in the dotted chain. The rules_bank
        # sometimes writes the method in upper case (e.g. .../POST.responses)
        # — accept both cases and normalize to lower-case in the key.
        m = re.match(
            r"^(.+?)[./](get|put|post|delete|patch|head|options)(?:[./]|$)",
            rest,
            flags=re.IGNORECASE,
        )
        if m:
            return f"op:{m.group(1)}:{m.group(2).lower()}"
        return None

    # path_parameter / query_parameter: openapi_object is paths.<path>.
    if rule_type in ("path_parameter", "query_parameter"):
        # No method in the rules_bank for these → group per path.
        return f"path:{rest}"

    return None


def _legacy_schemas_referenced_by(legacy: Optional[Dict[str, Any]], path: str, method: str) -> Set[str]:
    """Schema names a legacy fragment references via $ref."""
    if not legacy:
        return set()
    op = ((legacy.get("paths") or {}).get(path) or {}).get(method) or {}
    return set(re.findall(r"#/components/schemas/([A-Za-z0-9_]+)", str(op)))


def _rule_touches_op(
    path: str,
    method: str,
    schema_refs: Set[str],
    rule: Dict[str, Any],
) -> bool:
    """
    Pass 1 filter: is this rule plausibly about the legacy op (path, method)?

    Uses rule_type to know whether the rule has a method, just a path, or only
    a schema name — and matches accordingly.
    """
    key = _rule_group_key(rule)
    if key is None:
        return False
    if key.startswith("op:"):
        _, key_path, key_method = key.split(":", 2)
        return path in key_path and method == key_method
    if key.startswith("path:"):
        _, key_path = key.split(":", 1)
        return path in key_path  # path-level params apply to every method
    if key.startswith("schema:"):
        _, schema_name = key.split(":", 1)
        return schema_name in schema_refs
    return False


def _format_rule(idx: int, rule: Dict[str, Any]) -> str:
    mapping = rule.get("openapi_mapping") or {}
    obj = mapping.get("openapi_object", "")
    field = mapping.get("openapi_field", "")
    obj_with_field = f"{obj}.{field}" if field else obj
    text = (rule.get("rule_text") or "").replace("\n", " ")
    return (
        f"- [{idx}] type={rule.get('rule_type', '?')} "
        f"source={rule.get('source_name', '?')!r}\n"
        f"      maps to : {obj_with_field}\n"
        f"      text    : {text}"
    )


def _format_candidates(rules: List[Dict[str, Any]], indices: List[int]) -> str:
    if not indices:
        return "(no candidate rule matched this operation deterministically)"
    return "\n".join(_format_rule(i, rules[i]) for i in indices)


def _rag_query(path: str, method: str, rules: List[Dict[str, Any]], indices: List[int]) -> str:
    parts: List[str] = []
    if method and path:
        parts.append(f"{method.upper()} {path}")
    seen: Set[str] = set()
    for i in indices[:6]:
        name = (rules[i].get("source_name") or "").strip()
        if name and name not in seen:
            parts.append(name)
            seen.add(name)
        if len(seen) >= 2:
            break
    return " — ".join(parts) if parts else path


def _spec_rag(retriever, query: str, k: int = 4) -> str:
    try:
        chunks = retriever(query, k=k)
    except Exception as e:
        logger.warning(f"Planner → 3GPP RAG failed ({type(e).__name__}: {e})")
        return ""
    return "\n\n---\n\n".join(chunks) if chunks else ""


def _openapi_rag(query: str) -> str:
    try:
        from openapi_generator.tools.rag_tools import search_openapi_reference
        return search_openapi_reference(query) or ""
    except Exception as e:
        logger.warning(f"Planner → OpenAPI reference RAG failed ({type(e).__name__}: {e})")
        return ""


def _schemas_attached_to(op: TargetOperation, rules: List[Dict[str, Any]]) -> List[str]:
    """Schema names the rules already attached to this operation define.

    Reuses _rule_group_key, so the names come from whatever the rules bank
    happens to contain — nothing here knows any particular schema or service.
    """
    names: List[str] = []
    for rid in op.source_rule_ids:
        if not 0 <= rid < len(rules):
            continue
        key = _rule_group_key(rules[rid]) or ""
        if key.startswith("schema:"):
            name = key.split(":", 1)[1]
            if name not in names:
                names.append(name)
    return names


def _format_existing_plan(
    ops: List[TargetOperation],
    rules: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Render the plan. With `rules`, each entry also lists the schemas it carries.

    Phase C is asked whether an operation uses a given schema, and a type is
    usually reached indirectly — through another schema the operation carries.
    Without those names the question cannot be answered honestly, so the caller
    passes `rules` when the answer depends on that chain.
    """
    if not ops:
        return "(plan is empty so far)"
    lines = []
    for i, op in enumerate(ops):
        line = (
            f"- [{i}] {op.action:>7} {op.method.upper():>6} {op.path}  "
            f"(rules so far: {len(op.source_rule_ids)})"
        )
        if rules is not None:
            attached = _schemas_attached_to(op, rules)
            line += "\n        schemas carried: " + (
                ", ".join(attached) if attached else "(none yet)"
            )
        lines.append(line)
    return "\n".join(lines)


def _unconsumed_rule_gaps(
    rules: List[Dict[str, Any]],
    consumed_rule_ids: Set[int],
) -> List[str]:
    """Rules that never made it into any TargetOperation — reported for debug."""
    gaps: List[str] = []
    for i, rule in enumerate(rules):
        if i in consumed_rule_ids:
            continue
        gaps.append(
            f"[rule {i}] {rule.get('rule_type', '?')} / "
            f"{rule.get('source_name', '?')}"
        )
    return gaps


# ── Main node ────────────────────────────────────────────────────────────────

def planner_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
    logger.info("Planner Node started (2 passes + gap check).")

    rules_bank = state.get("rules_bank") or {}
    rules: List[Dict[str, Any]] = rules_bank.get("rules") or []
    legacy: Optional[Dict[str, Any]] = state.get("legacy_openapi")

    if not rules and not legacy:
        logger.warning("Planner → no rules and no legacy; empty plan")
        return {"operations_plan": [], "current_op_idx": 0}

    if llm is None:
        from openapi_generator.config.llm_config import get_llm
        llm = get_llm()

    if retriever is None:
        from openapi_generator.rag.retriever import get_relevant_chunks
        retriever = get_relevant_chunks

    consumed_rule_ids: Set[int] = set()
    operations: List[TargetOperation] = []

    # ── PASS 1 — polish the legacy ───────────────────────────────────────────
    legacy_ops = _list_legacy_ops(legacy)
    logger.info(f"Planner Pass 1 → {len(legacy_ops)} legacy operation(s) to review.")

    pass1_chain = pass1_prompt | llm.with_structured_output(PlannerPass1Verdict)

    for path, method in legacy_ops:
        schema_refs = _legacy_schemas_referenced_by(legacy, path, method)
        candidate_idx = [
            i for i, r in enumerate(rules)
            if _rule_touches_op(path, method, schema_refs, r)
        ]
        op_label = f"{method.upper()} {path}"
        logger.debug(f"  Pass 1 op={op_label} candidates={len(candidate_idx)}")

        query = _rag_query(path, method, rules, candidate_idx)
        spec_rag = _spec_rag(retriever, query) or "(no 3GPP RAG context)"
        oa_ref = _openapi_rag(query) or "(no OpenAPI reference context)"

        try:
            verdict: PlannerPass1Verdict = pass1_chain.invoke({
                "op_label": op_label,
                "legacy_fragment": _legacy_fragment_yaml(legacy, path, method),
                "candidate_rules": _format_candidates(rules, candidate_idx),
                "spec_rag": spec_rag,
                "openapi_reference": oa_ref,
            })
        except Exception as e:
            logger.error(f"Planner Pass 1 → LLM failed for {op_label}: {e}", exc_info=True)
            # Conservative fallback: keep the legacy op untouched.
            verdict = PlannerPass1Verdict(action="keep", source_rule_ids=[], rationale="")

        # Filter source_rule_ids to the candidate set — never trust LLM-invented ids.
        kept_ids = [i for i in verdict.source_rule_ids if i in candidate_idx]
        consumed_rule_ids.update(kept_ids)
        operations.append(TargetOperation(
            path=path,
            method=method,  # type: ignore[arg-type]
            action=verdict.action,
            source_rule_ids=kept_ids,
            priority="medium",
            rationale=verdict.rationale,
        ))
        logger.info(
            f"  Pass 1 op={op_label} action={verdict.action} "
            f"rules_used={len(kept_ids)}/{len(candidate_idx)}"
        )

    # ── PASS 2 — group residual rules by destination, then process ───────────
    residual_indices = [i for i in range(len(rules)) if i not in consumed_rule_ids]
    op_groups: Dict[Tuple[str, str], List[int]] = {}
    path_groups: Dict[str, List[int]] = {}
    schema_groups: Dict[str, List[int]] = {}
    security_groups: Dict[str, List[int]] = {}
    unknown: List[int] = []

    for i in residual_indices:
        key = _rule_group_key(rules[i])
        if key is None:
            unknown.append(i)
            continue
        if key.startswith("op:"):
            _, p, m = key.split(":", 2)
            op_groups.setdefault((p, m), []).append(i)
        elif key.startswith("path:"):
            _, p = key.split(":", 1)
            path_groups.setdefault(p, []).append(i)
        elif key.startswith("schema:"):
            _, name = key.split(":", 1)
            schema_groups.setdefault(name, []).append(i)
        elif key.startswith("security:"):
            # A security scheme belongs to the document, not to one operation.
            # Attach it to every operation so whichever Patcher call runs first
            # defines it under components; the Assembler keeps the first
            # definition on a name collision.
            _, name = key.split(":", 1)
            security_groups.setdefault(name, []).append(i)
        else:
            unknown.append(i)

    logger.info(
        f"Planner Pass 2 → {len(op_groups)} op group(s), "
        f"{len(path_groups)} path group(s), "
        f"{len(schema_groups)} schema group(s), "
        f"{len(unknown)} unclassified rule(s) "
        f"from {len(residual_indices)} residual rule(s)"
    )

    # ── PHASE A — op groups: LLM picks new or attach ────────────────────────
    pass2_chain = pass2_prompt | llm.with_structured_output(PlannerPass2Op)

    for (path, method), idx_list in op_groups.items():
        op_label = f"{method.upper()} {path}"
        query = _rag_query(path, method, rules, idx_list)
        spec_rag = _spec_rag(retriever, query) or "(no 3GPP RAG context)"
        oa_ref = _openapi_rag(query) or "(no OpenAPI reference context)"

        try:
            verdict: PlannerPass2Op = pass2_chain.invoke({
                "op_label": op_label,
                "grouped_rules": _format_candidates(rules, idx_list),
                "existing_plan": _format_existing_plan(operations),
                "spec_rag": spec_rag,
                "openapi_reference": oa_ref,
            })
        except Exception as e:
            logger.error(f"Planner Phase A → LLM failed for {op_label}: {e}", exc_info=True)
            verdict = PlannerPass2Op(
                destination="new",
                path=path, method=method,  # type: ignore[arg-type]
                source_rule_ids=idx_list, priority="medium", rationale="LLM fallback",
            )

        kept_ids = [i for i in verdict.source_rule_ids if i in idx_list]

        if verdict.destination == "attach" and 0 <= verdict.attach_to_index < len(operations):
            target = operations[verdict.attach_to_index]
            existing = set(target.source_rule_ids)
            for rid in kept_ids:
                if rid not in existing:
                    target.source_rule_ids.append(rid)
                    existing.add(rid)
            consumed_rule_ids.update(kept_ids)
            logger.info(
                f"  Phase A group={op_label} → attached {len(kept_ids)} rule(s) "
                f"to plan[{verdict.attach_to_index}] "
                f"({target.method.upper()} {target.path})"
            )
        else:
            if verdict.destination == "attach":
                logger.warning(
                    f"  Phase A group={op_label} → 'attach' with invalid index "
                    f"{verdict.attach_to_index}; falling back to 'new'"
                )
            consumed_rule_ids.update(kept_ids)
            operations.append(TargetOperation(
                path=verdict.path or path,
                method=verdict.method or method,  # type: ignore[arg-type]
                action="create",
                source_rule_ids=kept_ids,
                priority=verdict.priority,
                rationale=verdict.rationale,
            ))
            logger.info(
                f"  Phase A group={op_label} → new op "
                f"{(verdict.method or method).upper()} {(verdict.path or path)} "
                f"with {len(kept_ids)}/{len(idx_list)} rule(s)"
            )

    # ── PHASE B — path-level rules: attach to every op of the same path ─────
    for raw_path, idx_list in path_groups.items():
        # Path stored in keys may carry server prefix; match by substring.
        targets = [
            j for j, op in enumerate(operations)
            if op.path in raw_path or raw_path in op.path
        ]
        if not targets:
            logger.warning(
                f"  Phase B path={raw_path!r} → no op in plan matches; "
                f"{len(idx_list)} rule(s) left to ungrouped epilogue"
            )
            unknown.extend(idx_list)
            continue
        for j in targets:
            target = operations[j]
            existing = set(target.source_rule_ids)
            for rid in idx_list:
                if rid not in existing:
                    target.source_rule_ids.append(rid)
                    existing.add(rid)
        consumed_rule_ids.update(idx_list)
        logger.info(
            f"  Phase B path={raw_path!r} → attached {len(idx_list)} rule(s) "
            f"to {len(targets)} op(s)"
        )

    # ── PHASE B2 — security schemes: document-wide, no LLM call ─────────────
    # Unlike a schema, which an operation may or may not use, a securityScheme
    # is a document-level component: there is nothing to decide about which
    # operation owns it. Attach it to every operation so the first Patcher call
    # defines it under components — the Assembler keeps the first definition
    # when a component name collides.
    for scheme_name, idx_list in security_groups.items():
        if not operations:
            logger.warning(
                f"  Phase B2 security={scheme_name!r} → plan has no operation; "
                f"{len(idx_list)} rule(s) left as gaps"
            )
            unknown.extend(idx_list)
            continue
        for op in operations:
            existing = set(op.source_rule_ids)
            for rid in idx_list:
                if rid not in existing:
                    op.source_rule_ids.append(rid)
                    existing.add(rid)
        consumed_rule_ids.update(idx_list)
        logger.info(
            f"  Phase B2 security={scheme_name!r} → attached {len(idx_list)} "
            f"rule(s) to all {len(operations)} op(s)"
        )

    # ── PHASE C — schema groups: LLM picks which ops use the schema ─────────
    schema_chain = pass2_schema_prompt | llm.with_structured_output(PlannerSchemaAttachment)
    explicit_gaps: List[str] = []

    for schema_name, idx_list in schema_groups.items():
        oa_ref = _openapi_rag(schema_name) or "(no OpenAPI reference context)"
        try:
            attach: PlannerSchemaAttachment = schema_chain.invoke({
                "schema_name": schema_name,
                "schema_rules": _format_candidates(rules, idx_list),
                "existing_plan": _format_existing_plan(operations, rules),
                "openapi_reference": oa_ref,
            })
        except Exception as e:
            logger.error(f"Planner Phase C → LLM failed for schema={schema_name}: {e}", exc_info=True)
            attach = PlannerSchemaAttachment(attach_to_indices=[], rationale="LLM fallback")

        valid_targets = [j for j in attach.attach_to_indices if 0 <= j < len(operations)]
        if valid_targets:
            for j in valid_targets:
                target = operations[j]
                existing = set(target.source_rule_ids)
                for rid in idx_list:
                    if rid not in existing:
                        target.source_rule_ids.append(rid)
                        existing.add(rid)
            consumed_rule_ids.update(idx_list)
            logger.info(
                f"  Phase C schema={schema_name} → attached {len(idx_list)} rule(s) "
                f"to {len(valid_targets)} op(s)"
            )
        else:
            for rid in idx_list:
                rule = rules[rid]
                explicit_gaps.append(
                    f"[rule {rid}] schema_property / {rule.get('source_name', '?')} "
                    f"(schema {schema_name} not used by any op)"
                )
            logger.info(
                f"  Phase C schema={schema_name} → no op uses it; "
                f"{len(idx_list)} rule(s) flagged as gaps"
            )

    # ── Gap report ──────────────────────────────────────────────────────────
    # explicit_gaps carries the "schema X unused by any op" notes; the rest is
    # whatever ended up unconsumed (unclassified rule_type, Phase A 'attach'
    # with no kept_ids, etc.).
    leftover = _unconsumed_rule_gaps(rules, consumed_rule_ids)
    gaps = explicit_gaps + leftover
    if gaps:
        logger.info(
            f"Planner gap check → {len(gaps)} unmapped rule(s) "
            f"({len(explicit_gaps)} schema-only, {len(leftover)} other)"
        )

    # ── Assemble final ExtractionPlan ────────────────────────────────────────
    by_action = {a: sum(1 for o in operations if o.action == a) for a in ("create", "update", "keep", "discard")}
    document_summary = (
        ((rules_bank.get("metadata") or {}).get("source_document") or "").split("/")[-1]
        or "(unknown source)"
    )

    plan = ExtractionPlan(
        document_summary=document_summary,
        operations_to_generate=operations,
        probable_gaps=gaps,
    )
    ops_dict = [op.model_dump() for op in operations]

    logger.info(
        f"Planner Node complete — {len(operations)} operation(s): "
        f"{by_action['create']} create / {by_action['update']} update / "
        f"{by_action['keep']} keep / {by_action['discard']} discard; "
        f"rules covered: {len(consumed_rule_ids)}/{len(rules)}; "
        f"gaps: {len(gaps)}"
    )

    return {
        "operations_plan": ops_dict,
        "current_op_idx": 0,
        "extraction_plan": plan.model_dump(),
    }
