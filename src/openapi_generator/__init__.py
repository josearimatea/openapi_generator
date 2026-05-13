"""
OpenAPI generation pipeline (parallel to the chat RAG).

Inputs:
    - rules_bank.json   : structured rules produced by the openapi_rulesbank repo
    - legacy OpenAPI    : an older YAML spec (e.g. TS28532_ProvMnS.yaml)
    - RAG-3GPP          : reuses the chat RAG Qdrant collection
    - RAG-spec (opt.)   : optional RAG over the legacy OpenAPI itself

Output:
    - new OpenAPI YAML aligned with the rules bank, reusing the legacy spec
      where applicable.

The graph is per-operation (path + method) with a retry loop on the Validator.
"""

from openapi_generator.graph.openapi_gen_graph import build_openapi_gen_graph

__all__ = ["build_openapi_gen_graph"]
