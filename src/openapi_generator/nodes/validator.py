"""
Validator — turns the Reflector's confirmed problems into fixes for the Patcher.

One LLM call over the assembled document and the problems already weighed. This
is where analysis becomes action: the Reflector says what is wrong, the
Validator says what to do, and the Patcher does it. Keeping the two apart means
an account of a problem is not shaped by a solution someone had in mind.

Each fix is one action in one place — change, add or remove — so the Patcher
edits the document it produced rather than generating it again.

Nothing to fix ends the corrections: the routing in graph.conditions sends the
document on when `validation_errors` comes back empty.

State reads : final_openapi, fragment_reflection
State writes: validation_errors — the fixes, in the shape the routing and the
                                  Patcher's correction task expect
              validator_op_reflection — the summary carried into the next pass
"""

from typing import Any, Dict, List

import yaml

from openapi_generator.config import get_logger

logger = get_logger(__name__)


def _document_yaml(document: Dict[str, Any], limit: int = 24000) -> str:
    try:
        text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    except Exception as e:
        logger.warning(f"Validator → could not serialise the document: {e}")
        return "(the document could not be serialised)"
    return text if len(text) <= limit else text[:limit] + f"\n… truncated at {limit} characters"


def _format_problems(problems: List[Dict[str, Any]]) -> str:
    if not problems:
        return "(none)"
    lines = []
    for p in problems:
        line = f"  - {p.get('where', '?')}: {p.get('problem', '?')}"
        if p.get("severity"):
            line += f" [{p['severity']}]"
        if p.get("rule_ids"):
            line += f" (rules {p['rule_ids']})"
        lines.append(line)
    return "\n".join(lines)


def _format_list(items: List[Any]) -> str:
    return "\n".join(f"  - {item}" for item in items) if items else "(none)"


def validator_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
    reflection = state.get("fragment_reflection") or {}
    problems = reflection.get("problems") or []

    if not problems:
        logger.info("Validator → nothing to fix")
        return {
            "validation_errors": [],
            "validator_op_reflection": {
                "summary": reflection.get("summary", ""),
                "missing_rules": [],
            },
        }

    document = state.get("final_openapi") or {}

    if llm is None:
        from openapi_generator.config.llm_config import get_llm
        llm = get_llm()

    from openapi_generator.nodes.reflector import document_rag_context
    from openapi_generator.prompts.validator_prompts import validator_prompt
    from openapi_generator.schemas.operation import ValidatorVerdict

    # A fix has to state the conforming shape, so the specification comes along;
    # judging fidelity was the Reflector's job and needs no 3GPP passages here.
    _, reference = document_rag_context(
        document,
        (state.get("rules_bank") or {}).get("rules") or [],
        state.get("operations_plan") or [],
        retriever=None,
    )

    try:
        chain = validator_prompt | llm.with_structured_output(
            ValidatorVerdict, method="function_calling"
        )
        verdict: ValidatorVerdict = chain.invoke({
            "document_yaml": _document_yaml(document),
            "confirmed_problems": _format_problems(problems),
            "unapplied_rules": _format_list(reflection.get("unapplied_rule_ids") or []),
            "ungrounded": _format_list(reflection.get("ungrounded") or []),
            "reflector_summary": reflection.get("summary") or "(none)",
            "openapi_reference": reference or "(no OpenAPI reference)",
        })
    except Exception as e:
        logger.error(f"Validator → LLM call failed: {e}", exc_info=True)
        # Without fixes there is nothing the Patcher could act on, and looping
        # on the same document would only repeat the failure.
        return {
            "validation_errors": [],
            "validator_op_reflection": {
                "summary": "the validator did not run; the document was accepted as it stands",
                "missing_rules": [],
            },
        }

    # 'correction' is what the routing counts towards a retry; every fix asks
    # the Patcher to change the document, whichever action it names.
    errors = [
        {
            "error_type": "correction",
            "stage": "document",
            "action": fix.action,
            "ref": fix.where,
            "instruction": fix.instruction,
            "rule_ids": fix.rule_ids,
        }
        for fix in verdict.fixes
    ]

    by_action: Dict[str, int] = {}
    for fix in verdict.fixes:
        by_action[fix.action] = by_action.get(fix.action, 0) + 1
    logger.info(
        f"Validator → {len(errors)} fix(es): "
        + (", ".join(f"{n} {a}" for a, n in sorted(by_action.items())) or "none")
    )

    return {
        "validation_errors": errors,
        "validator_op_reflection": {
            "summary": verdict.summary or reflection.get("summary", ""),
            "missing_rules": [],
        },
    }
