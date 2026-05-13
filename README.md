# openapi_generator

LangGraph-based OpenAPI generation pipeline. Consumes a structured rules bank
(produced by `openapi_rulesbank`) plus an optional legacy OpenAPI YAML and
emits a new spec, one operation at a time, with a retry loop on the Validator.

## Layout

```
openapi_generator/
├── pyproject.toml
└── src/
    └── openapi_generator/
        ├── graph/        # LangGraph definition (state, conditions, build fn)
        ├── nodes/        # loader, planner, patcher, reflector, validator, assembler
        ├── schemas/      # Pydantic models exchanged between nodes
        └── prompts/      # (LLM prompt templates — to fill in)
```

## Install (editable, from a sibling project)

```bash
uv add --editable ../openapi_generator
# or
pip install -e ../openapi_generator
```

## Usage

```python
from openapi_generator import build_openapi_gen_graph

graph = build_openapi_gen_graph()  # pass checkpointer=... for persistence
state = graph.invoke({
    "spec_doc_path": "data/specs/28532-i00.md",
    "rules_bank": {...},
    "legacy_openapi": None,
    "openapi_target_path": "data/outputs/28532.yaml",
})
```

## Status

All nodes except `loader` are stubs. The graph topology and per-operation
retry loop are wired and ready to host the real implementations.
