# OpenAPI Generator — Briefing dos Nodes

Este documento descreve cada node do grafo LangGraph em `src/openapi_generator/`,
o que precisa ser implementado para sair do stub atual, e o contexto necessário
para um agente continuar o trabalho.

---

## Contexto geral

**Objetivo do pacote:** dado um spec 3GPP (markdown), um `rules_bank.json`
(produzido pelo repo irmão `openapi_rulesbank`) e opcionalmente um OpenAPI
legado em YAML, produzir um novo OpenAPI YAML alinhado às regras.

**Topologia do grafo (já implementada em `graph/openapi_gen_graph.py`):**

```
[loader] → [planner] → [patcher] → [reflector] → [validator] ──→ [assembler] → END
                          ↑                          │                │
                          │   error rate > threshold │   next op      │
                          │   AND retries < max      │                │
                          └────── retry op ──────────┘                │
                          └─────────────── next op ──────────────────┘
```

**Unidade de loop:** uma operação OpenAPI = `(path, method)`. O grafo processa
operações sequencialmente. Por operação, pode retentar o Patcher até
`MAX_OP_ITERATIONS` (3) vezes baseado no feedback do Validator.

**Estado compartilhado:** `OpenAPIGenState` em `graph/state.py` (TypedDict).
Cada node lê alguns campos e escreve outros — veja anotações no arquivo.

**Configuração de retry (`graph/conditions.py`):**
- `MAX_OP_ITERATIONS = 3`
- `VALIDATION_ERROR_THRESHOLD = 0.10` (10% dos itens marcados como `correction`
  dispara retry)

---

## Pré-requisitos antes de implementar nodes

O pacote hoje **não tem** infraestrutura de LLM nem de RAG. Precisa ser
adicionada antes (ou em paralelo) à implementação dos nodes:

### 1. LLM config (`src/openapi_generator/config/llm_config.py` — criar)
- Cliente OpenAI/Anthropic com chave via env (`OPENAI_API_KEY` etc.)
- Modelo padrão (sugestão: `gpt-4o` ou `claude-opus-4-7`)
- Helper para structured output via Pydantic (`with_structured_output()` do
  langchain ou equivalente)

### 2. RAG client (`src/openapi_generator/rag/` — criar)
- Cliente Qdrant (`qdrant-client`) configurado via env (`QDRANT_HOST`,
  `QDRANT_PORT`, `COLLECTION_NAME`)
- Embedder (HuggingFace `sentence-transformers/all-MiniLM-L6-v2` ou similar)
- Função `search_3gpp(query: str, k: int = 5) -> list[Document]` para o
  Patcher/Reflector puxarem contexto adicional
- (Opcional) RAG-spec: busca semântica no próprio OpenAPI legado para
  encontrar fragmentos reusáveis

### 3. Prompts (`src/openapi_generator/prompts/` — pasta existe, vazia)
- Um arquivo por node: `planner_prompts.py`, `patcher_prompts.py`, etc.
- Templates de prompt + few-shot examples
- Espelhar o estilo do `openapi_rulesbank/src/openapi_rulesbank/prompts/`
  (mesmo projeto, mesma ideia, abordagem similar)

### 4. Dependências do `pyproject.toml`
Adicionar:
```toml
"langchain-openai>=0.3",         # ou langchain-anthropic
"langchain-qdrant>=0.2",
"langchain-huggingface>=0.3",
"sentence-transformers>=5.2",
"qdrant-client>=1.15",
"openai>=1.106",
"python-dotenv>=1.1",
```

---

## Nodes — um por um

Todos estão em `src/openapi_generator/nodes/`. Cada arquivo já tem docstring
detalhando inputs/outputs e propósito. Abaixo, o resumo prático.

### `loader.py` ✅ JÁ IMPLEMENTADO (deterministic, sem LLM)

**Função:** lê o spec markdown 3GPP, separa em seções por `## ` header.

**Inputs do state:** `spec_doc_path`, `rules_bank`, `legacy_openapi`,
`openapi_target_path`.

**Outputs do state:**
- `parsed_spec_sections`: `[{section_id, title, content}, ...]`
- Inicializa `current_op_idx=0`, `op_iteration_count=0`,
  `validated_fragments_by_op={}`, `validation_errors=[]`
- Inicializa `final_openapi` com skeleton vazio (`openapi`, `info`, `paths`,
  `components.schemas`)

**Nota:** está completo. Talvez precise estender `final_openapi` skeleton
com `info` real puxado do `rules_bank` (título, versão, descrição).

---

### `planner.py` 🔴 STUB — 1 LLM call

**Função:** decide quais operações `(path, method)` gerar, em que ordem, e
quais regras do `rules_bank` fundamentam cada uma.

**Inputs do state:** `rules_bank`, `legacy_openapi`, `parsed_spec_sections`.

**Output esperado (escrever em `operations_plan`):**
```python
[
    {
        "path": "/{MnSRoot}/ProvMnS/{MnSVersion}/...",
        "method": "put",
        "action": "create" | "update" | "keep",
        "source_rule_ids": [12, 47, 88],  # índices em rules_bank["rules"]
        "priority": "high" | "medium" | "low",
    },
    ...
]
```

**Implementação:**
1. Agrupar `rules_bank["rules"]` por `openapi_object`/`path` (campos do schema
   do rulesbank).
2. Cruzar com `legacy_openapi.paths` para decidir `action`:
   - path existe no legacy + regras pedem mudanças → `update`
   - path existe e regras alinham → `keep`
   - path não existe → `create`
3. Chamar LLM com `ExtractionPlan` (já definido em `schemas/operation.py`) como
   output schema.
4. Ordenar por `priority` antes de retornar.

**Prompt deve incluir:**
- Sumário do `rules_bank` (count por openapi_object)
- Lista de paths do legacy_openapi (se houver)
- Instrução: gerar plano enxuto, sem duplicatas, ordem por dependência
  (schemas referenciados antes de paths que os usam)

---

### `patcher.py` 🔴 STUB — 1 LLM call + RAG por operação

**Função:** para a operação atual (`operations_plan[current_op_idx]`), produz
um `OperationFragment` OpenAPI completo (path + method + components.schemas
referenciados).

**Inputs do state:** `operations_plan`, `current_op_idx`, `rules_bank`,
`legacy_openapi`, `validation_errors` (retry), `validator_op_reflection`
(retry).

**Output esperado:**
- `current_fragment`: dict no formato:
  ```python
  {
      "path": "/...",
      "method": "put",
      "paths": {"/...": {"put": {...operação OpenAPI completa...}}},
      "components": {"schemas": {...schemas novos referenciados...}},
  }
  ```
- `op_iteration_count`: incrementa em 1

**Implementação:**
1. Pegar `op = operations_plan[current_op_idx]`.
2. Coletar regras: `[rules_bank["rules"][i] for i in op["source_rule_ids"]]`.
3. Se `op["action"] in ("update", "keep")`: extrair fragmento existente de
   `legacy_openapi["paths"][op["path"]][op["method"]]`.
4. Se regras não cobrem tudo: chamar `search_3gpp()` para puxar contexto
   adicional do spec.
5. **Em retry** (`op_iteration_count > 0`): incluir no prompt:
   - `validation_errors` filtrados a `error_type == "correction"`
   - `validator_op_reflection` da iteração anterior (instrução do que ajustar)
6. Chamar LLM com schema `OperationFragment` (já em `schemas/operation.py`).

**Prompt deve incluir:**
- Operação alvo (path, method, action)
- Regras aplicáveis (texto integral do rules_bank)
- Fragmento legado (se houver)
- Contexto 3GPP via RAG (se buscou)
- Em retry: feedback estruturado da iteração anterior

---

### `reflector.py` 🔴 STUB — LLM + RAG, 2 fases

**Função:** revisa o fragmento do Patcher em duas fases.

**Inputs do state:** `current_fragment`, `rules_bank`,
`validated_fragments_by_op`, `validator_op_reflection` (anterior).

**Outputs esperados:**
- `reflected_fragment`: o fragmento revisado (pode ser igual ao input se não
  achou nada a corrigir)
- `fragment_reflection`: análise de completude:
  ```python
  {
      "missing_items": [
          {"type": "response_schema", "status_code": "400", "reason": "..."},
          {"type": "security", "reason": "..."},
      ],
      "confidence": 0.85,
  }
  ```

**Implementação:**

**Fase 1 — por item:** para cada item do fragmento (cada property de schema,
cada parameter, cada response):
- CoT self-reflection: "isto está correto à luz da regra X?"
- Pode chamar RAG-3GPP se a regra parecer incompleta
- Anota `confidence`, `reasoning`, sugestão (`keep` / `flag` / `split` / `discard`)

**Fase 2 — por operação:** análise de completude:
- Faltam responses para erros comuns (400, 404, 500)?
- Faltam security schemes?
- Faltam exemplos?
- **Saída desta fase NÃO vai para o Patcher** — vai para o Validator stage 3.

**Espelho:** o `reflector` do `openapi_rulesbank` segue exatamente essa
estrutura. Vale ler `openapi_rulesbank/src/openapi_rulesbank/nodes/reflector.py`
para referência de implementação.

---

### `validator.py` 🔴 STUB — estrutural (sem LLM) + semântico (3 estágios LLM)

**Função:** valida o `reflected_fragment` e decide se aceita ou retenta.

**Inputs do state:** `reflected_fragment`, `fragment_reflection`,
`validator_op_reflection` (anterior), `validated_fragments_by_op`.

**Outputs esperados:**
- `validation_errors`: lista de problemas
  ```python
  [
      {
          "error_type": "correction" | "discard" | "split",
          "stage": "structural" | "semantic_1" | "semantic_2" | "semantic_3",
          "instruction": "texto explicando o que ajustar",
          "ref": "$.paths./foo.put.responses.400",  # JSONPath opcional
      },
  ]
  ```
- `validator_op_reflection`: instrução consolidada para o Patcher na próxima
  iteração:
  ```python
  {
      "summary": "principais ajustes necessários",
      "missing_rules": ["regra X não foi aplicada", ...],
      "structural_issues": [...],
      "semantic_priorities": [...],
  }
  ```
- `validated_fragments_by_op`: resumo compacto dos fragmentos aceitos até
  agora (usado pelo Reflector da próxima op)

**Implementação:**

**Etapa estrutural (sem LLM):**
1. `OperationFragment(**reflected_fragment)` — Pydantic check.
2. YAML round-trip: `yaml.safe_load(yaml.safe_dump(fragment))` para detectar
   tipos não-serializáveis.
3. Resolver `$ref` dentro do `final_openapi` acumulado + `current_fragment` —
   refs quebrados viram `error_type="correction"`.
4. **Não** rodar `openapi-spec-validator` aqui (fragmento parcial sempre
   falha). Roda só no Assembler final.

**Etapa semântica (3 estágios LLM em ordem):**
- **Stage 1 (op init):** reconciliar `validator_op_reflection` da iteração
  anterior — confirmar que as correções foram feitas
- **Stage 2 (per-item verdict):** para cada item do fragmento, verdict em
  `valid` / `correction` / `split` / `discard` + `new_missing_rules`
  (regras que apareceram durante validação)
- **Stage 3 (reflector review):** consumir `fragment_reflection` e gerar o
  `validator_op_reflection` final

**Roteamento (já implementado em `conditions.py`):** só `correction` conta
para o threshold de retry. `split` e `discard` são tratados via instrução no
`validator_op_reflection` na próxima iteração.

---

### `assembler.py` 🔴 STUB — deterministic, sem LLM

**Função:** acumula o fragmento aceito no documento final, salva snapshot,
avança o loop. Chamado uma vez por operação.

**Inputs do state:** `reflected_fragment`, `validation_errors`,
`op_iteration_count`, `final_openapi`, `openapi_target_path`, `current_op_idx`,
`operations_plan`.

**Outputs esperados:**
- `final_openapi`: documento OpenAPI acumulado (merge)
- `final_output_path`: caminho do arquivo escrito
- `current_op_idx`: incrementado em 1
- `op_iteration_count`: zerado
- Limpa `current_fragment`, `validation_errors`, `fragment_reflection`,
  `validator_op_reflection`

**Implementação:**

1. **Force-include em max retries:** se
   `op_iteration_count >= MAX_OP_ITERATIONS` e ainda há `correction` errors,
   aceitar o fragmento com flag (ex: adicionar
   `x-openapi-gen-warnings: [...]` no operation object).

2. **Deep merge:**
   ```python
   final_openapi["paths"] = deep_merge(final_openapi["paths"], fragment["paths"])
   final_openapi["components"] = deep_merge(final_openapi["components"], fragment["components"])
   ```
   Cuidado com colisões em `components.schemas`: schema já existente vs novo
   schema com mesmo nome — preferir manter o existente, logar conflito.

3. **Snapshot:** salvar `final_openapi` em
   `data/outputs/<thread_id>/op_<idx>.yaml` (ou similar) — útil para debug e
   resume.

4. **Avançar loop:** incrementar `current_op_idx`, zerar `op_iteration_count`,
   limpar campos per-op.

5. **Última operação:** se
   `current_op_idx == len(operations_plan) - 1`:
   - Rodar `openapi_spec_validator.validate(final_openapi)` para sanity check
     do doc completo
   - Escrever YAML final em `openapi_target_path`

**Dica:** o `assembler` do `openapi_rulesbank` (`builder.py` no rulesbank) tem
lógica parecida de merge + save.

---

## Ordem sugerida de implementação

1. **Infraestrutura primeiro** (LLM config, RAG client, prompts vazios).
2. **Planner** — sem ele, nada roda. Pode começar com versão simples
   (lista todas as ops do legacy_openapi sem filtrar).
3. **Loader extensions** — popular `final_openapi.info` do rules_bank.
4. **Assembler** — também sem LLM, mais simples. Permite testar fluxo
   end-to-end com Patcher/Reflector/Validator stub.
5. **Patcher** — primeiro o coração. Implementação completa incluindo retry.
6. **Validator estrutural** — sem LLM, pega bugs cedo.
7. **Reflector fase 1** — per-item, mais simples que fase 2.
8. **Validator semântico stage 2** — verdict per-item.
9. **Reflector fase 2** + **Validator stages 1 & 3** — completude e
   reconciliação.

A cada passo, rodar `pytest tests/` (estrutura a criar) e o smoke test:

```python
from openapi_generator import build_openapi_gen_graph

g = build_openapi_gen_graph()
result = g.invoke({
    "spec_doc_path": "data/inputs/28532-i00.md",
    "rules_bank": {...},
    "legacy_openapi": None,
    "openapi_target_path": "data/outputs/test.yaml",
})
```

---

## Referências dentro do monorepo

- **Repo irmão com pipeline análoga (já implementada):**
  `../openapi_rulesbank/src/openapi_rulesbank/`
  - `nodes/extractor.py`, `nodes/reflector.py`, `nodes/validator.py`,
    `nodes/builder.py` — espelhos diretos dos nodes deste pacote
  - `rag/retriever.py`, `rag/qdrant_factory.py` — base para o setup de RAG aqui
  - `config/llm_config.py`, `config/settings.py` — base para o setup de LLM
  - `prompts/*.py` — estilo de prompt a seguir

- **Schema do rules_bank** (input deste pacote, output do rulesbank):
  `../openapi_rulesbank/src/openapi_rulesbank/schemas/output.py` — classe
  `RulesBank`, formato JSON que chega no `Planner`/`Patcher`

- **Cliente RAG já existente:**
  `../openapi_chatbotUI/src/app/retrieval/retriever.py` — chat usa Qdrant
  com a mesma coleção `3gpp_rel18_28` que este pacote vai consumir

- **Como o pacote é consumido em produção:**
  `../openapi_chatbotUI/src/app/graph/openapi_gen_graph.py` — injeta
  SqliteSaver e compila o grafo
  `../openapi_chatbotUI/src/app/services/openapi_gen_service.py` — orquestra
  via stream/SSE

---

## Estado atual (commit inicial)

```
src/openapi_generator/
├── __init__.py                  (exporta build_openapi_gen_graph)
├── graph/
│   ├── state.py                 (OpenAPIGenState TypedDict — completo)
│   ├── conditions.py            (roteamento — completo)
│   └── openapi_gen_graph.py     (build fn — completo)
├── nodes/
│   ├── loader.py                ✅ implementado
│   ├── planner.py               🔴 stub
│   ├── patcher.py               🔴 stub
│   ├── reflector.py             🔴 stub
│   ├── validator.py             🔴 stub
│   └── assembler.py             🔴 stub
├── schemas/
│   └── operation.py             (TargetOperation, ExtractionPlan,
│                                 OperationFragment, ValidationVerdict)
└── prompts/                     (vazio — criar)
```

Dependências atuais (`pyproject.toml`): `langgraph`, `pydantic`, `pyyaml`.

Falta adicionar: cliente LLM, cliente Qdrant, embedder, dotenv.
