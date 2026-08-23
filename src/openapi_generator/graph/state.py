"""
Shared state for the OpenAPI generation graph.

Loop unit: one OpenAPI operation (path + HTTP method).
The graph processes operations sequentially. Per operation, it can retry
the Patcher up to MAX_ITERATIONS times based on Validator feedback.

Field ownership (which node writes what):
    spec_doc_path, rules_bank, legacy_openapi, openapi_target_path
                                          ← Loader inputs
    parsed_spec_sections                  ← Loader (parses spec markdown)
    operations_plan                       ← Planner
    current_op_idx, op_iteration_count    ← managed by Patcher/Assembler
    current_fragment                      ← Patcher
    reflected_fragment, fragment_reflection
                                          ← Reflector
    validation_errors, validator_op_reflection, validated_fragments_by_op
                                          ← Validator
    final_openapi, final_output_path      ← Assembler
    messages                              ← LangGraph (managed)
"""

from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph.message import add_messages


class OpenAPIGenState(TypedDict, total=False):
    # ── Inputs ────────────────────────────────────────────────
    spec_doc_path: str                    # path to the 3GPP markdown spec (e.g. 28532-i00.md)
    rules_bank: Dict[str, Any]            # parsed rules_bank JSON for this spec
    legacy_openapi: Optional[Dict[str, Any]]   # parsed legacy OpenAPI YAML (optional)
    openapi_target_path: str              # where the final YAML will be saved

    # ── Loader output ─────────────────────────────────────────
    # Sections parsed from spec_doc_path: [{section_id, title, content}]
    parsed_spec_sections: List[Dict[str, Any]]

    # ── Planner output ────────────────────────────────────────
    # List of operations to generate, ordered. Each item:
    #   {
    #     "path": "/{MnSRoot}/ProvMnS/{MnSVersion}/...",
    #     "method": "put",
    #     "action": "create" | "update" | "keep",
    #     "source_rule_ids": [int, ...],     # indices into rules_bank["rules"]
    #     "priority": "high" | "medium" | "low",
    #   }
    operations_plan: List[Dict[str, Any]]

    # ── Per-operation loop control ────────────────────────────
    current_op_idx: int
    op_iteration_count: int

    # ── Patcher output (per operation) ────────────────────────
    # OpenAPI fragment for the current op:
    #   {
    #     "paths": {"<path>": {"<method>": {...}}},
    #     "components": {"schemas": {...}, "parameters": {...}, ...},
    #   }
    current_fragment: Dict[str, Any]

    # What the Patcher retrieved to write each operation, kept for the
    # Reflector: reviewing a fragment against a different context than the one
    # it was written from is how a sound fragment comes to look wrong.
    #   {"<method> <path>": {"rag_context": str, "openapi_reference": str}}
    op_context: Dict[str, Any]

    # ── Reflector output ──────────────────────────────────────
    reflected_fragment: Dict[str, Any]
    fragment_reflection: Dict[str, Any]   # Phase-2 completeness analysis (input to Validator stage 3)

    # ── Validator output ──────────────────────────────────────
    # validation_errors items:
    #   {"error_type": "correction"|"discard"|"split", "stage": str, "instruction": str, "ref": str}
    validation_errors: List[Dict[str, Any]]
    # Validator's final per-op instruction back to the Patcher on retry:
    validator_op_reflection: Dict[str, Any]
    # Compact summary of accepted fragments so far (for Reflector context):
    validated_fragments_by_op: Dict[str, Any]

    # ── Assembler output ──────────────────────────────────────
    final_openapi: Dict[str, Any]         # accumulated full OpenAPI doc
    final_output_path: str                # written file path

    # ── LangGraph-managed conversation history (for streaming/UX) ─
    messages: Annotated[List[Dict[str, Any]], add_messages]
