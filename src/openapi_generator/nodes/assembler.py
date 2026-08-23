"""
Assembler — deterministic, no LLM.

Runs once per operation. Merges the fragment produced by the Patcher
(refined by the Reflector, accepted by the Validator) into the accumulated
final OpenAPI document, then advances the per-operation loop.

Responsibilities per call:
  1. Force-include guard: if op_iteration_count >= MAX_OP_ITERATIONS and the
     fragment still has 'correction' errors, accept it anyway, annotating
     `x-openapi-gen-warnings` on the operation so nothing is silently lost.
  2. Deep-merge:
       final_openapi['paths']      ← reflected_fragment['paths']
       final_openapi['components'] ← reflected_fragment['components']
     Existing schemas under components.schemas are preserved on name collision
     (legacy / earlier fragments win) and the conflict is logged.
  3. Track the validated fragment in `validated_fragments_by_op` so the
     Reflector can see what was accepted so far when reviewing future ops.
  4. Advance the loop: current_op_idx += 1, op_iteration_count = 0, and
     clear the per-op scratch fields.
  5. On the last operation, write the final YAML to openapi_target_path
     and report its path back via `final_output_path`.

State reads:
    reflected_fragment        — fragment to merge (path+method+paths+components)
    validation_errors         — to detect force-include scenario
    op_iteration_count        — to detect force-include scenario
    final_openapi             — accumulator (seeded by Loader)
    current_op_idx            — index of the operation just processed
    operations_plan           — to know whether this was the last op
    openapi_target_path       — destination for the final YAML
    validated_fragments_by_op — accumulator of accepted fragments

State writes (return dict):
    final_openapi             — updated accumulator
    final_output_path         — path written (only on the last op; else "")
    current_op_idx            — advanced by 1
    op_iteration_count        — reset to 0
    current_fragment          — cleared
    reflected_fragment        — cleared
    validation_errors         — cleared
    fragment_reflection       — cleared
    validator_op_reflection   — cleared
    validated_fragments_by_op — updated with the just-merged fragment
"""

import copy
from pathlib import Path
from typing import Any, Dict, List

import yaml

from openapi_generator.config import get_logger
from openapi_generator.graph.conditions import MAX_OP_ITERATIONS

logger = get_logger(__name__)


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any], path: str = "") -> None:
    """Recursively merge `src` into `dst` in-place.

    Dict + dict → recurse. Anything else → src wins, except under
    `components.schemas`: when both sides have the same schema name, keep
    the destination (legacy / earlier fragment wins) and log the conflict.
    """
    for key, src_val in src.items():
        if key not in dst:
            dst[key] = src_val
            continue
        dst_val = dst[key]
        here = f"{path}.{key}" if path else key

        # Conflict on a schema name → keep existing, log.
        if here.startswith("components.schemas.") and here.count(".") == 2:
            logger.warning(
                f"Schema conflict at {here}: keeping existing definition, "
                "discarding new one from this fragment."
            )
            continue

        if isinstance(dst_val, dict) and isinstance(src_val, dict):
            _deep_merge(dst_val, src_val, here)
        else:
            dst[key] = src_val


def _force_include_annotation(
    fragment: Dict[str, Any],
    correction_errors: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return a copy of `fragment` with x-openapi-gen-warnings injected.

    Only used when the retry budget is exhausted but corrections remain.
    Adds the warning to every operation block under fragment.paths so the
    issue surfaces in the final YAML and is not lost.
    """
    annotated = copy.deepcopy(fragment)
    warnings = [
        e.get("instruction", "")
        for e in correction_errors
        if e.get("instruction")
    ] or ["unresolved correction errors at max retries"]

    for path, ops in (annotated.get("paths") or {}).items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if isinstance(op, dict):
                op["x-openapi-gen-warnings"] = warnings
    return annotated


_GENERATOR_KEY = "  x-openapi-generator:"
_SEPARATOR = "  # " + "-" * 68


class _IndentedDumper(yaml.SafeDumper):
    """Indent list items under the key that owns them.

    PyYAML writes a sequence flush with its parent key, which reads as though
    the item sat at the parent's level. Published OpenAPI documents indent it,
    so generated files match what a reader is used to.
    """

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _rule_after_generator_block(text: str) -> str:
    """Draw a comment rule where the generator's own block ends.

    `info` opens with how the document was produced and continues with what the
    specification says it is. The two read as one list otherwise, so a rule is
    inserted at the boundary: the first line after the x-openapi-generator
    block that is indented as a sibling key.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith(_GENERATOR_KEY))
    except StopIteration:
        return text

    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith("    "):
            if not line.startswith("  "):
                break  # info ended without any spec field following
            lines.insert(i, _SEPARATOR)
            return "\n".join(lines) + "\n"
    return text


def _write_final_yaml(final_openapi: Dict[str, Any], target_path: str) -> str:
    """Write the final OpenAPI document to disk. Returns the absolute path."""
    p = Path(target_path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    # The Loader reserves the optional top-level blocks so the document comes
    # out in canonical order; the ones nothing filled are dropped here rather
    # than published empty.
    document = {
        key: value
        for key, value in final_openapi.items()
        if value or key in ("openapi", "info", "paths")
    }
    text = yaml.dump(
        document,
        Dumper=_IndentedDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    # safe_dump writes no comments, so the rule separating how the document was
    # produced from what it describes is drawn afterwards. It sits between the
    # x-openapi-generator block and the specification's own info fields, which
    # the Loader ordered that way.
    text = _rule_after_generator_block(text)
    with p.open("w", encoding="utf-8") as f:
        f.write(text)
    logger.info(f"Assembler → wrote final OpenAPI to {p}")
    return str(p)


def assembler_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
    # llm, retriever: unused (deterministic node) — accepted for uniform DI.
    plan = state.get("operations_plan") or []
    current_idx = state.get("current_op_idx", 0)
    op = plan[current_idx] if 0 <= current_idx < len(plan) else {}
    op_key = f"{op.get('method', '?')} {op.get('path', '?')}"

    fragment = state.get("reflected_fragment") or {}
    iteration = state.get("op_iteration_count", 0)
    errors = state.get("validation_errors") or []
    correction_errors = [e for e in errors if e.get("error_type") == "correction"]

    # ── 1. Force-include guard ────────────────────────────────────
    if iteration >= MAX_OP_ITERATIONS and correction_errors:
        logger.warning(
            f"Assembler → force-include {op_key} at max retries "
            f"with {len(correction_errors)} unresolved correction(s)"
        )
        fragment = _force_include_annotation(fragment, correction_errors)

    # ── 2. Deep-merge (or remove on discard) ─────────────────────
    final_openapi = copy.deepcopy(state.get("final_openapi") or {})
    final_openapi.setdefault("paths", {})
    final_openapi.setdefault("components", {}).setdefault("schemas", {})

    frag_paths: Dict[str, Any] = {}
    frag_components: Dict[str, Any] = {}

    if op.get("action") == "discard":
        op_path = op.get("path", "")
        op_method = (op.get("method") or "").lower()
        path_item = (final_openapi.get("paths") or {}).get(op_path)
        if isinstance(path_item, dict) and op_method in path_item:
            path_item.pop(op_method, None)
            if not path_item:
                final_openapi["paths"].pop(op_path, None)
            logger.info(f"Assembler → discarded {op_key} from final_openapi")
        else:
            logger.warning(
                f"Assembler → discard requested for {op_key} but it was "
                "not present in final_openapi (already removed?)"
            )
    else:
        frag_paths = fragment.get("paths") or {}
        frag_components = fragment.get("components") or {}
        if frag_paths:
            _deep_merge(final_openapi["paths"], frag_paths, "paths")
        if frag_components:
            _deep_merge(final_openapi["components"], frag_components, "components")

        logger.info(
            f"Assembler → merged {op_key}: "
            f"+{len(frag_paths)} path block(s), "
            f"+{len((frag_components.get('schemas') or {}))} schema(s)"
        )

    # ── 3. Track accepted fragment ────────────────────────────────
    validated = dict(state.get("validated_fragments_by_op") or {})
    validated[op_key] = {
        "path": op.get("path", ""),
        "method": op.get("method", ""),
        "source_rule_ids": op.get("source_rule_ids", []),
        "paths_added": list(frag_paths.keys()),
        "schemas_added": list((frag_components.get("schemas") or {}).keys()),
    }

    # ── 4. Advance loop ───────────────────────────────────────────
    next_idx = current_idx + 1
    is_last = next_idx >= len(plan)

    # ── 5. Write YAML on the last op ──────────────────────────────
    final_output_path = ""
    target = state.get("openapi_target_path") or ""
    if is_last and target:
        try:
            final_output_path = _write_final_yaml(final_openapi, target)
        except Exception as e:
            logger.error(f"Assembler → failed to write final YAML: {e}", exc_info=True)

    return {
        "final_openapi": final_openapi,
        "final_output_path": final_output_path,
        "current_op_idx": next_idx,
        "op_iteration_count": 0,
        "current_fragment": {},
        "reflected_fragment": {},
        "validation_errors": [],
        "fragment_reflection": {},
        "validator_op_reflection": {},
        "validated_fragments_by_op": validated,
    }
