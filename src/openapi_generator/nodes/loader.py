"""
Loader — deterministic but for one LLM call, on the cover page.

Responsibilities:
1. Parse the 3GPP markdown spec into sections (one per ``## `` header).
2. Seed final_openapi:
     - if legacy_openapi was provided in the state → start from a deep copy
       of it (preserves info / servers / paths / components), adding only the
       provenance extensions.
     - otherwise → start from an empty skeleton stamped with
       settings.OPENAPI_VERSION, filling info and externalDocs from the
       specification's cover page and the provenance from rules_bank.metadata.
   The cover states the document's number, version, subject and copyright, but
   a source arrives as PDF or DOCX and each converts that page to a different
   shape, so it is read by an LLM rather than matched against a pattern.
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
    """An empty document with its top-level keys already in canonical order.

    The YAML is written without sorting, so insertion order is what a reader
    sees. Declaring the blocks here — even the ones filled in later, by the
    cover-page pass and by the Patcher's servers pass — keeps the output laid
    out the way a hand-written OpenAPI document is, instead of trailing
    `externalDocs` and `servers` after `components`.
    """
    from openapi_generator.config.settings import OPENAPI_VERSION

    return {
        "openapi": OPENAPI_VERSION,
        "info": {},
        "externalDocs": {},
        "servers": [],
        "paths": {},
        "components": {"schemas": {}},
    }


def _document_blocks(spec_text: str, openapi_version: str, llm=None) -> Dict[str, Any]:
    """`info` and `externalDocs` values stated on the specification's cover.

    No rule covers these — they describe the document rather than the API — but
    the specification states them outright on its cover page, so they are read
    from there rather than left blank or invented. What the cover does not say
    is simply omitted.
    """
    from openapi_generator.utils.servers import document_metadata

    meta = document_metadata(spec_text, llm)
    if not meta:
        return {}

    info: Dict[str, Any] = {}
    number, subject = meta.get("number"), meta.get("subject")
    if number:
        info["title"] = f"TS {number}" + (f" {subject}" if subject else "")
    if meta.get("version"):
        info["version"] = meta["version"]

    described = f"TS {number} {subject}" if number and subject else "the service"
    description = f"OAS {openapi_version} definition of {described}"
    if meta.get("copyright"):
        description += f" {meta['copyright']}"
    info["description"] = description

    blocks: Dict[str, Any] = {"info": info}
    if meta.get("external_docs_url"):
        external = {"url": meta["external_docs_url"]}
        if meta.get("external_docs_description"):
            external["description"] = meta["external_docs_description"]
        blocks["externalDocs"] = external
    return blocks


def _provenance(rules_bank: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """How this document was produced, as one `x-openapi-generator` block.

    Kept as a single extension rather than a spread of sibling `x-` keys so
    that `info` still reads like the specification's own: title, version and
    description where a reader expects them, with the machinery gathered under
    one key that can be skipped over — or dropped — without touching the rest.

    Two stages are recorded because they are genuinely separate: the rules bank
    was produced by openapi_rulesbank, possibly by another model on another
    day, and this generation consumed it. A single `model` field would suggest
    one model did both.
    """
    from openapi_generator.config.settings import MODEL

    provenance: Dict[str, Any] = {
        "generator": "openapi_generator",
        "model": MODEL,
        "generated-at": datetime.now().astimezone().isoformat(),
    }

    meta = (rules_bank or {}).get("metadata") or {}
    if meta:
        bank: Dict[str, Any] = {}
        if meta.get("source_document"):
            bank["source-document"] = Path(meta["source_document"]).name
        if meta.get("generated_at"):
            bank["generated-at"] = meta["generated_at"]
        if meta.get("model"):
            bank["model"] = meta["model"]
        if meta.get("total_rules") is not None:
            bank["total-rules"] = meta["total_rules"]
        if bank:
            provenance["rules-bank"] = bank
    return provenance


def _seed_final_openapi(
    legacy_openapi: Optional[Dict[str, Any]],
    rules_bank: Optional[Dict[str, Any]],
    spec_text: str = "",
    llm=None,
) -> Dict[str, Any]:
    """Decide whether to start from the legacy spec or an empty skeleton."""
    if legacy_openapi:
        logger.info(
            "Loader → seeding final_openapi from legacy "
            f"({len(legacy_openapi.get('paths') or {})} path(s), "
            f"{len(((legacy_openapi.get('components') or {}).get('schemas') or {}))} schema(s))"
        )
        seeded = copy.deepcopy(legacy_openapi)
        # Keep the legacy's own info and record only how this run was produced.
        seeded["info"] = {
            "x-openapi-generator": _provenance(rules_bank),
            **(seeded.get("info") or {}),
        }
        return seeded

    skeleton = _empty_skeleton()
    # Provenance first, then what the specification says the document is: the
    # generated file announces its machinery up front and reads like the
    # original from there on.
    blocks = _document_blocks(spec_text, skeleton["openapi"], llm) if spec_text else {}
    skeleton["info"] = {
        "x-openapi-generator": _provenance(rules_bank),
        **blocks.get("info", {}),
    }
    if blocks.get("externalDocs"):
        skeleton["externalDocs"] = blocks["externalDocs"]
    logger.info(
        "Loader → no legacy provided; starting from empty skeleton "
        f"(cover-page metadata: {bool(blocks)}; "
        f"title: {skeleton['info'].get('title', '(none)')!r})"
    )
    return skeleton


def loader_node(state: dict, llm=None, retriever=None) -> Dict[str, Any]:
    # retriever unused; llm is used only to read the cover page (see
    # _document_blocks) — the rest of this node is deterministic.
    spec_path = state.get("spec_doc_path", "")
    parsed_sections: List[Dict[str, Any]] = []
    spec_text = ""

    if spec_path:
        p = Path(spec_path)
        if p.exists():
            try:
                spec_text = text = p.read_text(encoding="utf-8")
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
        "final_openapi": _seed_final_openapi(legacy_openapi, rules_bank, spec_text, llm),
    }
