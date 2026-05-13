"""
Loader — deterministic, no LLM.

Responsibilities:
1. Parse the 3GPP markdown spec into sections (one per ``## `` header).
2. Initialize loop counters and the empty final OpenAPI skeleton.

The rules_bank, legacy_openapi (optional) and openapi_target_path arrive
already parsed in the initial state from the service layer — the Loader does
not touch them, only forwards what's needed.

Inputs (state): spec_doc_path, rules_bank, legacy_openapi, openapi_target_path
Outputs (state): parsed_spec_sections, current_op_idx=0, op_iteration_count=0,
                 validated_fragments_by_op={}, validation_errors=[],
                 final_openapi (empty skeleton).
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _parse_sections(markdown_text: str) -> List[Dict[str, Any]]:
    """
    Split a 3GPP markdown spec on ``## `` headers.

    Returns a list of {section_id (int as str), title, content}.
    Mirrors the shape used by openapi_rulesbank so section_id values from the
    rules_bank line up with these indices.
    """
    pattern = re.compile(r"^## (.+)$", re.MULTILINE)
    matches = list(pattern.finditer(markdown_text))
    sections: List[Dict[str, Any]] = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown_text)
        sections.append({
            "section_id": str(idx),
            "title": m.group(1).strip(),
            "content": markdown_text[start:end].strip(),
        })
    return sections


def loader_node(state: dict) -> Dict[str, Any]:
    spec_path = state.get("spec_doc_path", "")
    parsed_sections: List[Dict[str, Any]] = []

    if spec_path:
        p = Path(spec_path)
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                parsed_sections = _parse_sections(text)
                logger.info(
                    f"Loader → parsed {len(parsed_sections)} sections from {p.name}"
                )
            except Exception as e:
                logger.error(f"Loader → failed to read spec at {p}: {e}", exc_info=True)
        else:
            logger.warning(f"Loader → spec_doc_path does not exist: {spec_path}")
    else:
        logger.warning("Loader → no spec_doc_path provided")

    return {
        "parsed_spec_sections": parsed_sections,
        "current_op_idx": 0,
        "op_iteration_count": 0,
        "validated_fragments_by_op": {},
        "validation_errors": [],
        "final_openapi": {
            "openapi": "3.0.3",
            "info": {},
            "paths": {},
            "components": {"schemas": {}},
        },
    }
