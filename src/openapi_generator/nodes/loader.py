"""
Loader — deterministic, no LLM.

Responsibilities:
1. Parse the 3GPP markdown spec into sections (one per ``## `` header).
2. Seed final_openapi:
     - if legacy_openapi was provided in the state → start from a deep copy
       of it (preserves info / servers / paths / components).
     - otherwise → start from an empty OpenAPI 3.0.3 skeleton, populating
       info from rules_bank.metadata when available.
3. Initialize loop counters and the empty accumulators consumed by the
   per-operation loop.

Inputs (state):
    spec_doc_path       (str)         — path to the 3GPP markdown
    rules_bank          (dict|None)   — already-parsed rules bank JSON
    legacy_openapi      (dict|None)   — already-parsed legacy OpenAPI (optional)
    openapi_target_path (str)         — output YAML target (forwarded only)

Outputs (state):
    parsed_spec_sections      — list[{section_id, title, content}]
    current_op_idx            = 0
    op_iteration_count        = 0
    validated_fragments_by_op = {}
    validation_errors         = []
    final_openapi             — deep copy of legacy OR seeded skeleton
"""

import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from openapi_generator.config import get_logger
from openapi_generator.utils.parsers import parse_sections

logger = get_logger(__name__)


def _empty_skeleton() -> Dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {},
        "paths": {},
        "components": {"schemas": {}},
    }


def _info_from_rules_bank(rules_bank: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a minimal info block from rules_bank metadata. Empty dict if none.

    Provenance covers two distinct stages, so each extension names the stage it
    belongs to: `x-rules-bank-*` describes the bank that fed this run (produced
    by openapi_rulesbank, possibly by a different model on a different day), and
    `x-generator-*` describes this generation. Without the split, a lone `model`
    field reads as though one model did both.
    """
    if not rules_bank:
        return {}
    from openapi_generator.config.settings import MODEL

    meta = rules_bank.get("metadata") or {}
    info: Dict[str, Any] = {}
    source = meta.get("source_document")
    if source:
        # Use the spec file stem as a placeholder title — Patcher can refine.
        info["title"] = f"OpenAPI generated from {Path(source).stem}"
    if source:
        info["x-rules-bank-source-document"] = Path(source).name
    if meta.get("generated_at"):
        info["x-rules-bank-generated-at"] = meta["generated_at"]
    if meta.get("model"):
        info["x-rules-bank-model"] = meta["model"]
    if meta.get("total_rules") is not None:
        info["x-rules-bank-total-rules"] = meta["total_rules"]

    info["x-generator-model"] = MODEL
    info["x-generator-generated-at"] = datetime.now().astimezone().isoformat()
    return info


def _seed_final_openapi(
    legacy_openapi: Optional[Dict[str, Any]],
    rules_bank: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Decide whether to start from the legacy spec or an empty skeleton."""
    if legacy_openapi:
        logger.info(
            "Loader → seeding final_openapi from legacy "
            f"({len(legacy_openapi.get('paths') or {})} path(s), "
            f"{len(((legacy_openapi.get('components') or {}).get('schemas') or {}))} schema(s))"
        )
        seeded = copy.deepcopy(legacy_openapi)
        # Keep the legacy's own info (title, version, description) and add only
        # the provenance extensions, so the document still records what produced
        # this run.
        provenance = {
            k: v for k, v in _info_from_rules_bank(rules_bank).items()
            if k.startswith("x-")
        }
        if provenance:
            seeded.setdefault("info", {}).update(provenance)
        return seeded

    skeleton = _empty_skeleton()
    skeleton["info"] = _info_from_rules_bank(rules_bank)
    logger.info(
        "Loader → no legacy provided; starting from empty skeleton "
        f"(info from rules_bank: {bool(skeleton['info'])})"
    )
    return skeleton


def loader_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
    # llm, retriever: unused (deterministic node) — accepted for uniform DI.
    spec_path = state.get("spec_doc_path", "")
    parsed_sections: List[Dict[str, Any]] = []

    if spec_path:
        p = Path(spec_path)
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                parsed_sections, excluded = parse_sections(text)
                logger.info(
                    f"Loader → parsed {len(parsed_sections)} sections from {p.name} "
                    f"(excluded {len(excluded)} symbolic-title section(s))"
                )
            except Exception as e:
                logger.error(f"Loader → failed to read spec at {p}: {e}", exc_info=True)
        else:
            logger.warning(f"Loader → spec_doc_path does not exist: {spec_path}")
    else:
        logger.warning("Loader → no spec_doc_path provided")

    rules_bank = state.get("rules_bank")
    legacy_openapi = state.get("legacy_openapi")

    rules_count = len((rules_bank or {}).get("rules") or [])
    logger.info(
        f"Loader → rules_bank: {rules_count} rule(s); "
        f"legacy_openapi: {'present' if legacy_openapi else 'absent'}"
    )

    return {
        "parsed_spec_sections": parsed_sections,
        "current_op_idx": 0,
        "op_iteration_count": 0,
        "validated_fragments_by_op": {},
        "validation_errors": [],
        "final_openapi": _seed_final_openapi(legacy_openapi, rules_bank),
    }
