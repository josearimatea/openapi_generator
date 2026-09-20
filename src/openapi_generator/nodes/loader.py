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
from openapi_generator.rag.spec_index import index_spec_sections
from openapi_generator.utils.parsers import normative_only, parse_sections

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
        # How this document was produced, ahead of the document itself: a
        # reader meets the machinery first and then an OpenAPI that reads
        # exactly like a hand-written one, rather than finding generator
        # bookkeeping wedged between `info` and the title it announces.
        "x-ai-generation": {},
        "openapi": OPENAPI_VERSION,
        "info": {},
        "externalDocs": {},
        "servers": [],
        "paths": {},
        "components": {"schemas": {}},
    }


def _document_blocks(
    cover_text: str,
    openapi_version: str,
    target: str = "",
    spec_collection: str = "",
    llm=None,
) -> Dict[str, Any]:
    """`info` and `externalDocs` values stated on the specification's cover.

    No rule covers these — they describe the document rather than the API — but
    the specification states them outright on its cover page, so they are read
    from there rather than left blank or invented. What the cover does not say
    is simply omitted.

    Takes the document as it is, not its normative text: the cover sits above
    the contents listing, which is exactly what the reduction to normative text
    drops.
    """
    from openapi_generator.utils.servers import document_metadata

    meta = document_metadata(cover_text, target, spec_collection, llm)
    if not meta:
        return {}

    # A target this specification does not define stops the run. Generating
    # anyway would title the document after a service that is not here and
    # produce something plausible and wrong; the services the specification
    # does define are named so the run can be corrected.
    if meta.get("service_unrecognised"):
        defined = meta.get("services_defined") or []
        raise ValueError(
            f"The specification does not define a service named "
            f"{meta['service_unrecognised']!r}. "
            + (
                f"It defines: {', '.join(defined)}."
                if defined
                else "No service name could be read from it."
            )
        )

    info: Dict[str, Any] = {}
    number, subject = meta.get("number"), meta.get("subject")
    # The title names the SERVICE this document describes, which is what a
    # published OpenAPI carries. The specification's own title is a fallback
    # for a run that named no target: better a document identified by the
    # specification it came from than one with no title at all.
    service = meta.get("service_title")
    if service:
        info["title"] = service
    elif number:
        info["title"] = f"TS {number}" + (f" {subject}" if subject else "")
    if meta.get("version"):
        info["version"] = meta["version"]

    described = service or (
        f"TS {number} {subject}" if number and subject else "the service"
    )
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
    """How this document was produced, as one `x-ai-generation` block.

    Kept as a single extension at the top of the document rather than a spread
    of `x-` keys inside `info`: the generation record is about the file, not
    about the API it describes, and `info` then reads like the specification's
    own — title, version and description where a reader expects them. Gathered
    under one key, the machinery can be skipped over, or dropped, without
    touching the rest.

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
    cover_text: str = "",
    target: str = "",
    spec_collection: str = "",
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
        # The legacy document keeps its own `info` untouched; only how this run
        # was produced is added, and it goes above the document rather than
        # inside it.
        return {"x-ai-generation": _provenance(rules_bank), **seeded}

    skeleton = _empty_skeleton()
    blocks = (
        _document_blocks(cover_text, skeleton["openapi"], target, spec_collection, llm)
        if cover_text else {}
    )
    skeleton["x-ai-generation"] = _provenance(rules_bank)
    skeleton["info"] = blocks.get("info", {})
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
    cover_text = ""

    if spec_path:
        p = Path(spec_path)
        if p.exists():
            try:
                # The cover page is read from the document as it is, and
                # everything else from its normative text. The two cannot be
                # the same string: `normative_only` drops the contents listing
                # along with the annexes, and the cover sits above the
                # listing — so reducing first would take the cover with it and
                # leave the document without a version or a copyright.
                raw = p.read_text(encoding="utf-8")
                cover_text = raw
                spec_text = text = normative_only(raw)
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

    # Index this specification before anything reads it. Some questions can
    # only be answered from the document at hand — what it calls the service,
    # where it says the service is hosted — and they are answered by meaning
    # rather than by pattern. The collection lasts this run; the Assembler
    # drops it when the document is final.
    spec_collection = index_spec_sections(parsed_sections) if parsed_sections else ""

    return {
        "parsed_spec_sections": parsed_sections,
        "spec_collection": spec_collection,
        "current_op_idx": 0,
        "op_iteration_count": 0,
        "validated_fragments_by_op": {},
        "validation_errors": [],
        "final_openapi": _seed_final_openapi(
            legacy_openapi,
            rules_bank,
            cover_text,
            state.get("target_mns", ""),
            spec_collection,
            llm,
        ),
    }
