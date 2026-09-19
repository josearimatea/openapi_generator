"""
Assembler — deterministic, no LLM.

Runs once per operation, directly after the Patcher: it merges the fragment
just produced into the accumulated document, writes the document out, and
advances the loop. Review comes afterwards, over the assembled whole, so what
the Reflector and the Validator read is a real document rather than a fragment.

Writing on every pass is deliberate, and mirrors how openapi_rulesbank's
Builder saves its bank after each section: the document grows on disk as it is
built, so a failure partway through costs the operations still to come rather
than the ones already done.

Responsibilities per call:
  1. Deep-merge:
       final_openapi['paths']      ← current_fragment['paths']
       final_openapi['components'] ← current_fragment['components']
     Existing schemas under components.schemas are preserved on name collision
     (legacy / earlier fragments win) and the conflict is logged.
     An operation planned as 'discard' is removed from the document instead.
  2. Write the document to openapi_target_path, reporting the path back via
     `final_output_path`.
  3. Track what was merged in `validated_fragments_by_op`.
  4. Advance the loop: current_op_idx += 1, op_iteration_count = 0, and clear
     the per-operation scratch fields.

State reads:
    current_fragment          — fragment to merge (path+method+paths+components)
    final_openapi             — accumulator (seeded by Loader)
    current_op_idx            — index of the operation just processed
    operations_plan           — the plan being worked through
    openapi_target_path       — destination for the YAML
    validated_fragments_by_op — accumulator of merged fragments

State writes (return dict):
    final_openapi             — updated accumulator
    final_output_path         — path written
    current_op_idx            — advanced by 1
    op_iteration_count        — reset to 0
    current_fragment          — cleared
    validated_fragments_by_op — updated with the just-merged fragment
"""

import copy
from pathlib import Path
from typing import Any, Dict, List

import yaml

from openapi_generator.config import get_logger

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
    # What the run spent, recorded in the document itself rather than only in a
    # log — the same place openapi_rulesbank puts it in its bank's metadata, so
    # a generated artefact carries the cost of generating it. Written on every
    # pass, so the figure is always the total so far.
    try:
        from openapi_generator.config.llm_config import usage_tracker
        from openapi_generator.config.settings import runtime_seconds
        usage = {"runtime_seconds": round(runtime_seconds(), 1), **usage_tracker.as_dict()}
        info = document.get("info")
        if usage["llm_calls"] and isinstance(info, dict) \
                and isinstance(info.get("x-openapi-generator"), dict):
            # Copy down to the block being changed: the shallow filter above
            # shares `info` with the accumulator in the state, and a figure
            # meant for the file should not become part of the state.
            document["info"] = {
                **info,
                "x-openapi-generator": {**info["x-openapi-generator"], "usage": usage},
            }
    except Exception as e:  # never let bookkeeping block a written document
        logger.debug(f"Assembler → usage unavailable: {e}")

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

    # Report what the run has spent so far. The document is written after every
    # operation, so this doubles as progress: a long plan shows its cost
    # climbing rather than arriving as one number at the end.
    try:
        from openapi_generator.config.llm_config import usage_tracker
        from openapi_generator.config.settings import runtime_seconds
        usage = usage_tracker.as_dict()
        if usage["llm_calls"]:
            logger.info(
                f"Assembler → {runtime_seconds():.0f}s in, "
                f"{usage['llm_calls']} call(s), "
                f"{usage['total_tokens']:,} tokens "
                f"({usage['prompt_tokens']:,} prompt / "
                f"{usage['completion_tokens']:,} completion"
                + (f", {usage['reasoning_tokens']:,} reasoning"
                   if usage["reasoning_tokens"] else "")
                + (f", {usage['cached_prompt_tokens']:,} cached"
                   if usage["cached_prompt_tokens"] else "")
                + ")"
                + (f" — US$ {usage['cost_usd']:.4f}" if usage["cost_usd"] else "")
            )
    except Exception as e:  # never let reporting break a written document
        logger.debug(f"Assembler → usage unavailable: {e}")

    return str(p)


def assembler_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
    # llm, retriever: unused (deterministic node) — accepted for uniform DI.
    plan = state.get("operations_plan") or []
    current_idx = state.get("current_op_idx", 0)
    op = plan[current_idx] if 0 <= current_idx < len(plan) else {}
    op_key = f"{op.get('method', '?')} {op.get('path', '?')}"

    # The Assembler now runs directly after the Patcher, so what it merges is
    # the fragment just produced. Review comes later, over the whole document.
    fragment = state.get("current_fragment") or {}

    # ── Deep-merge (or remove on discard) ────────────────────────
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

    # ── 2. Write the document, every pass ────────────────────────
    # Saving as it grows means an operation that fails late does not take the
    # ones already merged with it.
    final_output_path = ""
    target = state.get("openapi_target_path") or ""
    if target:
        try:
            final_output_path = _write_final_yaml(final_openapi, target)
        except Exception as e:
            logger.error(f"Assembler → failed to write the YAML: {e}", exc_info=True)

    # ── 3. Advance loop ──────────────────────────────────────────
    return {
        "final_openapi": final_openapi,
        "final_output_path": final_output_path,
        "current_op_idx": current_idx + 1,
        "op_iteration_count": 0,
        "current_fragment": {},
        "validated_fragments_by_op": validated,
    }
