"""
CLI entry point for the OpenAPI generation pipeline.

Runs the full LangGraph flow end-to-end with the defaults defined in this
package:
    - LLM: ChatOpenAI built from openapi_generator.config.settings (.env)
    - Retriever: Qdrant collection from settings if reachable; otherwise the
      pipeline runs without RAG context (graceful degradation).

Inputs are plain files on disk — the CLI does not depend on any other project:
    --spec-doc        3GPP markdown spec to consume.
    --rules-bank      JSON file whose parsed contents become state["rules_bank"].
                      Format is the package's own contract (see graph/state.py);
                      the producer is irrelevant.
    --legacy-openapi  Optional OpenAPI YAML/JSON file whose parsed contents
                      become state["legacy_openapi"].
    --output          Path where the generated OpenAPI YAML will be written.

Usage:
    python -m openapi_generator.main \\
        --spec-doc data/inputs/28532-i00.md \\
        --rules-bank data/inputs/rules_bank.json \\
        --output data/outputs/28532.yaml

    python -m openapi_generator.main \\
        --spec-doc spec.md --rules-bank rb.json \\
        --legacy-openapi data/inputs/legacy.yaml \\
        --output out.yaml
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

from openapi_generator.config import get_logger
from openapi_generator.graph.openapi_gen_graph import build_openapi_gen_graph

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the OpenAPI generation pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python -m openapi_generator.main \\\n"
            "      --spec-doc data/inputs/28532-i00.md \\\n"
            "      --rules-bank data/inputs/rules_bank.json \\\n"
            "      --output data/outputs/28532.yaml\n"
        ),
    )
    parser.add_argument(
        "--spec-doc",
        required=True,
        metavar="PATH",
        help="Path to the 3GPP markdown spec consumed by the Loader.",
    )
    parser.add_argument(
        "--rules-bank",
        required=True,
        metavar="PATH",
        help=(
            "Path to a JSON file whose parsed contents are injected into "
            "state['rules_bank']. The expected shape is defined by this "
            "package (see graph/state.py); origin of the file is irrelevant."
        ),
    )
    parser.add_argument(
        "--target",
        default=None,
        metavar="MnS",
        help=(
            "The management service this document describes, as the "
            "specification names it (e.g. ProvMnS). A specification defines "
            "several and this document covers one, so the name decides what "
            "info.title says. Optional once the rules bank records the target "
            "it was built for; given here, it takes precedence."
        ),
    )
    parser.add_argument(
        "--legacy-openapi",
        default=None,
        metavar="PATH",
        help=(
            "Optional OpenAPI YAML or JSON file. Parsed contents go to "
            "state['legacy_openapi']. Omit when there is no legacy spec."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="Path where the Assembler will write the final OpenAPI YAML.",
    )
    return parser.parse_args()


def _require_file(path: str, label: str) -> str:
    """Resolve to abs path and fail early if the file is missing."""
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        logger.error(f"{label} not found: {abs_path}")
        sys.exit(1)
    return abs_path


def _load_rules_bank(path: str) -> dict:
    """Parse the rules_bank JSON file into a dict for the graph state."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_legacy_openapi(path: Optional[str]) -> Optional[dict]:
    """Parse the legacy OpenAPI file (YAML or JSON). Returns None when omitted."""
    if not path:
        return None
    text = Path(path).read_text(encoding="utf-8")
    # JSON starts with '{' or '['; anything else is YAML.
    if text.lstrip().startswith(("{", "[")):
        return json.loads(text)
    return yaml.safe_load(text)


def main() -> None:
    args = _parse_args()

    # Validate every path before doing any work — avoids partial runs and
    # wasted LLM calls if a file is missing.
    spec_doc_path = _require_file(args.spec_doc, "Spec document")
    rules_bank_path = _require_file(args.rules_bank, "Rules bank")
    legacy_path = (
        _require_file(args.legacy_openapi, "Legacy OpenAPI")
        if args.legacy_openapi
        else None
    )

    # Resolve output path and ensure its parent directory exists so the
    # Assembler can write without extra setup.
    output_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # File parsing happens here, not inside the graph: the graph contract
    # works with already-parsed dicts, keeping the nodes free of disk I/O
    # (except the Loader, which reads spec_doc_path directly).
    rules_bank: dict = _load_rules_bank(rules_bank_path)
    legacy_openapi: Optional[dict] = _load_legacy_openapi(legacy_path)

    # The bank knows which service it was built for, so the argument is only
    # needed when it does not say — and overrides it when both are present.
    # (Banks produced before the field existed carry nothing, hence the
    # argument staying available rather than being replaced by it.)
    target = args.target or (rules_bank.get("metadata") or {}).get("target") or ""

    logger.info("Starting OpenAPI generation pipeline.")
    logger.info(f"  Spec doc      : {spec_doc_path}")
    logger.info(f"  Rules bank    : {rules_bank_path}")
    logger.info(f"  Target MnS    : {target or '(not given — title falls back to the specification)'}")
    logger.info(f"  Legacy OpenAPI: {legacy_path or '(none)'}")
    logger.info(f"  Output        : {output_path}")

    initial_state: dict[str, Any] = {
        "spec_doc_path": spec_doc_path,
        "rules_bank": rules_bank,
        "legacy_openapi": legacy_openapi,
        "target_mns": target,
        "openapi_target_path": output_path,
        "messages": [],
    }

    # No checkpointer / llm / retriever passed → defaults from settings/.env.
    # Host applications that need persistence or a different LLM should call
    # build_openapi_gen_graph(...) directly with their own overrides instead
    # of using this CLI.
    pipeline = build_openapi_gen_graph()

    result: dict = {}
    try:
        result = pipeline.invoke(initial_state)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        raise
    finally:
        # The Loader indexes this specification into a collection of its own,
        # and the graph's Finish node drops it at the end of a run. This is a
        # second attempt, harmless when the node already did it. It does NOT
        # cover a run that raised: `invoke` then returns nothing, `result`
        # stays empty and the collection's name is unknown here (PROBLEMS P23).
        from openapi_generator.rag.spec_index import drop_spec_collection
        drop_spec_collection(result.get("spec_collection", ""))

    # The Assembler writes the final YAML and reports its absolute path back
    # in state["final_output_path"]. Empty value = nothing was written.
    final_path = result.get("final_output_path", "")
    if final_path:
        logger.info("Pipeline complete.")
        logger.info(f"  OpenAPI saved to: {final_path}")
    else:
        logger.error("Pipeline finished but no output file was produced.")
        sys.exit(1)


if __name__ == "__main__":
    main()
