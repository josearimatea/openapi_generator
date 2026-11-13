# Problemas em aberto — OpenAPI Generator

Lista de rastreio dos defeitos conhecidos do pipeline. Cada entrada diz o que
acontece, de onde vem, como foi constatado e em que estado está. A intenção é
não perder um problema de vista entre uma sessão e outra, e não repetir
diagnóstico já feito.

O `RASTREIO.md` conta a história das decisões e o que se descobriu; aqui fica
apenas o que ainda está quebrado, ou o que foi resolvido e merece ficar
registrado como resolvido. Quando um problema é fechado, ele não é apagado — a
entrada recebe o estado e a evidência de que foi corrigido, porque um defeito
que reaparece é mais fácil de reconhecer do que de redescobrir.

Versionado desde o commit `c16ce9b` (antes a pasta `docs/` ficava no `.gitignore`).

Os itens do `openapi_rulesbank` citados aqui (N.x, D.x) estão em
`openapi_rulesbank/docs/problems/04_Problems_ProvMnS.txt` (documento vivo, 09/nov),
com o histórico em `03_…` (congelado em 09/nov) e nos legados `01_…` e `02_…`. O
`openapi_rulesbank/docs/problems/PROBLEMS_openapi_generator.md` é uma cópia deste
arquivo de 02/nov, não atualizada: a fonte é este.

Última atualização: 2026-11-12 (consertos rápidos: P10 parcial, P17, P18, P20, P22,
P23 parcial). Antes: 2026-11-10 (análise do código: causa do P10, P17–P25; alinhamento
com as decisões do rulesbank até 09/nov: R1, R3, P7, P9, P11–P16, D.10).
Antes: 2026-11-08 (P11–P16 — mudanças do banco do rulesbank a conferir aqui).
Antes: 2026-10-06 (P10 — vazamento do relatório do Patcher, medido pelo openapi_analysis);
2026-09-20 (correção por blocos, contexto do documento).

---

## Execução de referência

Mesmo banco (`rules_bank_28532-i20_full_20260915_214107.json`, 122 regras),
mesma spec (`28532-i20.md`), mesmo alvo
(`data/inputs/legacy/ProvMnS/rel_18_TS28532_ProvMnS.yaml`), sem legacy,
`openai/gpt-5.6-luna` via OpenRouter, RAG disponível.

| | base 15/set | 19/set | **20/set** |
|---|---|---|---|
| Saída | `…235054` | `…161507` | `…184338` |
| **Contrato** | 32/191 (17%) | **87/191 (46%)** | 80/191 (42%) |
| Metadata | — | 43% | **5/7 (71%)** |
| Regras cobertas | 97/122 | 121/122 | 121/122 |
| Schemas do alvo presentes | 5/16 | 16/16 | **16/16** |
| `$ref` quebrados | 16 | 0 | **0** |
| `components` dentro de `paths` | sim | sim | **não** |
| Laço de correção | 3 rodadas, pendências | 3 rodadas, 8 pendentes | **2 rodadas, converge** |
| Custo / tempo | US$ 0,166 / 537s | US$ 0,143 / 358s | **US$ 0,096 / 234s** |

A coluna de 20/set é a execução mais saudável até aqui — o laço converge
sozinho pela primeira vez, sem `$ref` quebrado e sem corrupção estrutural — e
mesmo assim o contrato **caiu 4 pontos**. As duas causas estão medidas e
nenhuma é dos consertos: 18 das 28 folhas perdidas são o posicionamento dos
parâmetros (ver **R1**, defeito do banco) e 10 são o `NotifyEvent` que saiu
degradado. Registrado para não confundir "execução saudável" com "melhor
número".

O PerfMnS (17 regras, 1 operação) sai em 18/19 (95%). A diferença de escala é o
que expôs os defeitos abaixo.

**Nota de instrumento:** uma tentativa de medir rodou com o Qdrant fora do ar. O
pipeline degrada em silêncio nesse caso — loga `RAG skipped` e segue — e o
resultado teria misturado a falta de RAG com o efeito da correção. Foi
interrompida. Convém uma flag que faça a ausência de RAG abortar uma execução de
medição em vez de degradá-la.

### O próximo banco de referência (09/nov) — ainda não rodado aqui

O rulesbank fechou um run completo da ProvMnS **com a errata** (`--aux
user_errata_28532.md`, commit `2c6a675` de lá):
`openapi_rulesbank/docs/run_provmns_20261109/banco.json`
(`rules_bank_28532-i20_full_20261109_102214.json`, 121 regras, 0 incluídas à força,
`unresolved_missing` vazio, 8 em `rules_never_completed`). Medido lá pelo
`openapi_analysis`: 87,7% do oficial reproduzido certo (93/106), com 13 regras erradas
de fato depois da revisão do juiz (ver R3). Não está em `data/inputs/rules/ProvMnS/`.

Três cuidados ao rodar com ele:

- **o banco não traz `metadata.target`.** O `main.py` cai no vazio e o título volta a
  ser o do documento; passar `--target ProvMnS`;
- **o alvo é o Rel-18 18.2.0** — o `rel_18_TS28532_ProvMnS.yaml` daqui já é esse. O
  rulesbank registra que o `TS28532_ProvMnS.yaml` sem prefixo é o 17.5.0 e não vale
  para a i20;
- **os números daqui e os de lá não se comparam.** O `eval/comparison.py` conta folhas
  do YAML (191 de contrato); o `openapi_analysis` conta regras oficiais (106 na
  ProvMnS desde o `698cbf8` de lá, que fez do `additionalProperties` da raiz uma regra
  só).

---

## P3 — Propriedades sem tipo no documento

**Estado:** aberto, em queda. 3 ocorrências em 20/set (eram 5 em 19/set, 7 na
execução intermediária).

O caso original — o callback `notifyMOICreation` com `href: {}`,
`notificationId: {}`, `eventTime: {}` — não ocorre mais, porque os schemas que o
descrevem voltaram com P1. As que restam são `value` e `oldValue` dentro de
tipos importados que o Patcher materializou localmente sabendo os nomes e não
os tipos.

**É P7 se manifestando**, e não tem conserto próprio: quando o tipo deixar de
ganhar definição local, a propriedade sem tipo vai junto.

---

## R1 — O banco endereça ao path parâmetros que o texto atribui a uma operação

**Estado:** resolvido no banco (conferido em 10/nov no banco de 09/nov); falta rodar
o generator com ele. **Era defeito do `openapi_rulesbank`, não do generator.**
**Impacto:** 18 das 28 folhas de contrato perdidas em 20/set.

**Conferido em 10/nov.** No banco de 09/nov os sete `query_parameter` endereçam a
operação: `paths./{className}={id}.get` (scope, filter, attributes, fields,
dataNodeSelector) e `paths./{className}={id}.delete` (scope, filter). O generator
já os põe na operação desde 01/out (`_rule_group_key`: o endereço decide). Os
`path_parameter` do path item continuam em `paths./{className}={id}`. A exceção
nova, `className` e `id` repetidos no DELETE, é outro defeito do banco (R3, N.35).

Cinco regras de `query_parameter` trazem um `rule_text` que nomeia a operação
explicitamente, e um `openapi_mapping` que endereça o path:

```
rule_text : "The HTTP GET operation supports an optional query parameter
             named scope ..."                          (idem filter,
                                                        attributes, fields,
                                                        dataNodeSelector)
openapi_object : paths./{className}={id}        ← o path, vale para todo método
deveria ser    : paths./{className}={id}.get    ← o que o texto diz
```

O texto está certo e o mapeamento está errado — **erro de mapeamento, não de
leitura**, o mesmo padrão que o `02_Problems_ProvMnS_LEGACY_ate_20261107.txt` §3.19 registrou para o
enum fragmentado.

A prova de que o Extractor sabe fazer certo está no próprio banco: a regra do
`filter` no DELETE mapeia para `paths./{className}={id}.delete`, com o nível da
operação. E o alvo confirma a divisão: `className`/`id` no path (valem para
todos os métodos), os cinco de query dentro do GET.

**O generator está correto e não deve mudar.** Ele escreve onde a regra
endereça, que é o comportamento desejado; "consertar" aqui exigiria saber que
estes cinco parâmetros pertencem ao GET — conhecimento de uma MnS específica,
proibido.

**Conserto no rulesbank, genérico:** quando o `rule_text` nomeia um método HTTP,
o `openapi_object` deve endereçar aquela operação e não o path. Texto e
mapeamento têm de concordar, e a discordância é verificável automaticamente.

---

## R2 — `NotifyEvent` sai degradado quando a operação gera muitos schemas

**Estado:** aberto, não diagnosticado.
**Impacto:** 10 das 28 folhas perdidas em 20/set.

`NotifyEvent` saiu como `{type: object}` — sem o `allOf`, sem propriedades —
enquanto em 19/set saiu completo. Os schemas vizinhos do mesmo grupo
(`NotifyMoiDeletion`, por exemplo) também perderam propriedades.

A diferença observável está na chamada que os produziu: o PUT gerou **17
schemas numa única resposta** em 20/set, contra 13 em 19/set. A suspeita é de
diluição — quanto mais schemas numa resposta, menos detalhe em cada um — mas
não está verificado.

Sem relação com a correção por blocos: isto acontece na geração da operação,
antes de qualquer rodada de revisão.

**Lado do banco (10/nov):** a definição de `NotifyEvent` estava numa cláusula sem
estilo de título (12.1.1.4.1a.10), que caía na seção anterior. O rulesbank resolveu
(N.10 e N.29 lá). No banco de 09/nov, `NotifyEvent` vem completo (`allOf`, 3
propriedades, `required [specificProblem]`). A degradação daqui acontecia na
geração e não depende disso, então continua sem diagnóstico. O banco novo é o teste
que separa as duas coisas.

---

## R3 — Defeitos do banco de 09/nov que vão aparecer no documento gerado

**Estado:** aberto, **do lado do `openapi_rulesbank`**. Registrado aqui para que,
ao rodar com aquele banco, nada disso seja diagnosticado como defeito do generator
ou "consertado" nele. Pela regra de sempre, o generator escreve o que a regra diz.

| Item lá | O que o banco traz | O que sai aqui | Correto (decisão lá) |
|---|---|---|---|
| N.35 | `className`/`id` também em `paths./{className}={id}.delete` | parâmetros duplicados no DELETE: parece a volta do "Parâmetros duplicados" resolvido aqui, mas a origem agora é a regra | só no nível do path |
| N.7 + SpecificProblem | `$ref` local para `ErrorResponseDefault/Get/Patch` e `SpecificProblem` (nunca completados) | `$ref` local para tipo que ninguém define (alimenta P7) | arquivo externo (`TS28623_ComDefs.yaml`, `TS28532_FaultMnS.yaml`), a decidir lá |
| N.34 | `additionalText` = `$ref AdditionalText` | mais um `$ref` local sem definição | `string` |
| N.36 | `MoiChange.value/oldValue` = `object` | rejeita escalares | `{}` (any type) |
| N.8 | `PatchItem` required com `value`; `Scope` com `scopeLevel` | required a mais | `[op, path]`; `Scope` sem required |
| N.31 | `Resource` sem o `oneOf`/`anyOf` da errata | composição faltando | `oneOf [objeto, anyOf NRMs]` |
| N.32, N.33, N.37 | GET 4XX/5XX com `ErrorResponseGet`; DELETE sem 4XX/5XX; `default` sobrando junto com 4XX+5XX | as mesmas formas, e uma contagem diferente na métrica | critério D.10 (ver "Não são defeitos") |

O banco **declara** os `$ref` que não completou, em `metadata.rules_never_completed`
(seção, regra, motivo). O generator não lê esse campo (P16). É a informação que o P7
pede para distinguir "schema que se perdeu" de "tipo que vem de fora".

---

## P7 — Tipo importado ganha definição local

**Estado:** aberto, com o lado do banco **mudado** (ver "O banco de 09/nov", no fim
desta entrada). **O maior dos que restam.** Em 20/set: 23 schemas contra os 16 do
alvo — os 7 a mais são tipos importados com definição inventada. (A subseção abaixo
cita 28 e 12: são os números de 19/set.)
**Origem:** `openapi_rulesbank/docs/problems/02_Problems_ProvMnS_LEGACY_ate_20261107.txt` §3.6 e §3.19(a).

### O que mudou depois de P1

Eu previa que os `$ref` para tipos importados continuassem quebrados depois da
correção da Phase C. Não foi o que aconteceu: **o Patcher passou a materializá-los
localmente**, inventando uma definição para cada. O documento tem 28 schemas onde
o alvo tem 16, e os 12 a mais são exatamente os tipos importados:

```
AttributeNameValuePairSet, AttributeValueChangeSet, DateTime, ErrorResponse,
ErrorResponseDefault, ErrorResponseGet, ErrorResponsePatch, Filter,
NotificationId, SpecificProblem, SystemDN, Uri
```

Tecnicamente válido — nenhuma referência aponta para o vazio, e por isso o
contador de `$ref` quebrados zerou. Semanticamente errado: um tipo importado com
definição local é uma cópia que diverge da original na próxima release do
documento de origem.

**A troca é ruim para diagnóstico.** Antes o defeito gritava (`$ref` apontando
para nada); agora é silencioso — um schema plausível a mais no meio de 28. Se o
número de `$ref` quebrados for usado como métrica, ele passa a dar verde para um
documento que piorou neste aspecto.

É também a raiz das propriedades sem tipo que restaram (5 ocorrências: `value` e
`oldValue` em `AttributeValueChangeSet`, `value` em outro ponto): o Patcher sabe
o nome da propriedade e não o tipo, porque o tipo está no outro documento.

Uma especificação importa tipos de outros documentos. Esses tipos não têm regra
de definição no banco — e não deveriam ter, porque quem os define é o outro
documento. Quando uma regra os referencia, o `openapi_value` costuma trazer só
`#/components/schemas/<Tipo>`, e o generator escreve isso literalmente: nasce uma
referência local para um schema que ninguém define.

O banco mediu o tamanho disso do lado dele: 33 `$ref` internos quebrados contra
9 externos corretos, e anotou que **os 33 são todos tipos importados**. Os 9 que
saíram certos vieram de seções onde o `allOf` já carregava o `$ref` completo — ou
seja, a informação chega quando está embutida no valor, e se perde quando não
está.

### Medido no banco i20 (2026-09-19) — a informação em grande parte não está lá

Minha primeira proposta era usar o campo `references` da regra para decidir o
documento de origem. **Medi, e ele quase sempre vem sem documento:**

```
referências com `file` preenchido :  9   (NotificationHeader 4, Dn 2,
                                          NotificationId 2,
                                          AttributeNameValuePairSet 1)
referências com `file` VAZIO      : 48   (ErrorResponse 17, Resource 3,
                                          Filter 3, CorrelatedNotification 3, …)
```

Os 9 preenchidos são exatamente os 9 `$ref` externos que o rulesbank mediu como
corretos — batem um a um. Ou seja: **onde o banco sabe o arquivo, ele já acerta;
onde não sabe, ninguém sabe.** O `schema_name` vem sempre, o `file` quase nunca.

Então não há vínculo escondido para o generator aproveitar. O que **existe** e o
generator não usa é a informação negativa:

```
schemas que o banco DEFINE         : 16
schemas REFERENCIADOS e não definidos : 15
  AttributeNameValuePairSet, AttributeValueChangeSet, DateTime, Dn,
  ErrorResponse, ErrorResponseDefault, ErrorResponseGet, ErrorResponsePatch,
  Filter, NotificationHeader, NotificationId, NotificationType,
  SpecificProblem, SystemDN, Uri
```

Esses 15 o banco referencia mas nunca define — e **não é omissão, é o formato**:
tipos importados não têm cláusula neste documento. O generator hoje não distingue
"schema que se perdeu no caminho" de "schema que nunca esteve aqui", e escreve
`$ref` local para os dois.

**O que dá para fazer sem o arquivo:** separar as duas populações, que é
computável do banco inteiro (define? / referencia?). Isso não produz o `$ref`
externo correto, mas impede que a ausência seja tratada como defeito a consertar,
e dá ao Reflector a diferença entre "falta um schema" e "este tipo vem de fora".

**O que NÃO dá:** inventar o nome do arquivo. Sem `file`, escrever
`TS28623_ComDefs.yaml` seria conhecimento de uma spec específica — proibido. O
conserto completo depende de o rulesbank preencher o `file`, e é lá que ele deve
ser pedido.

### O banco de 09/nov — o pedido foi atendido em grande parte (conferido em 10/nov)

O rulesbank implementou em 19/out uma cascata para o nome do arquivo (RASTREIO de
lá, "A solução implementada: cascata de resolução do filename"): helper_context →
anexo, lido só por regex e nunca por prompt → RAG → vazio com gap declarado. Desde
07/nov a errata dá os arquivos que só o Anexo A diz. No banco de 09/nov:

```
referências                       : 70
com `file` preenchido             : 31  (ErrorResponse 14, NotificationHeader 5,
                                         Filter 3, AttributeNameValuePairSet 3,
                                         Dn 2, NotificationId 2, Uri 1,
                                         AttributeValueChangeSet 1 — todos ComDefs)
sem `file`, tipo LOCAL (correto)  : Resource, Scope, PatchItem, NotifyMoi*, …
sem `file`, tipo IMPORTADO        : ErrorResponseDefault 3, ErrorResponseGet 3,
                                    ErrorResponsePatch 1, AdditionalText 1
                                    (+ SpecificProblem no valor)  → R3: N.7, N.34
```

O que muda aqui:

- para os tipos comuns, o `$ref` externo **chega pronto** na regra. Falta conferir
  que o generator o copia como está — o prompt diz "an external $ref is copied
  verbatim" — e que o Reflector não o trata como quebrado (o `_LOCAL_REF` só olha
  `#/components/schemas/…`, então um externo nunca é candidato: certo);
- o que sobra são os **defeitos declarados** do banco (R3). Eles estão em
  `metadata.rules_never_completed`, com o motivo. A separação "define? / referencia?"
  proposta acima continua valendo, e agora tem uma fonte melhor: o banco **diz**
  quais `$ref` ficaram incompletos. Usar esse campo é o P16;
- o mecanismo que inventa a definição local (Patcher ou correção materializando o
  tipo para o `$ref` resolver) não mudou. Com menos `$ref` locais pendentes, ele tem
  menos onde agir, mas age igual sobre os que sobram.

---

## P8 — Convenção de nomes entre chave de callback e nome de schema

**Estado:** aberto, não iniciado.
**Origem:** `openapi_rulesbank/docs/problems/02_Problems_ProvMnS_LEGACY_ate_20261107.txt` §3.7, verificado lá contra
cinco YAMLs.

As duas formas convivem no mesmo documento e **não são a mesma string**:

| | forma | exemplo |
|---|---|---|
| chave do callback | lowerCamelCase, siglas preservadas | `notifyMOIAttributeValueChanges` |
| nome do schema | UpperCamelCase, siglas normalizadas | `NotifyMoiAttributeValueChanges` |

O generator recebe as duas do banco e não as reconcilia. Se escrever a chave com
a forma do schema, ou o `$ref` com a forma da chave, a referência não resolve e
vira mais um caso de P2.

É regra de **forma**, não de conteúdo: não depende de que serviço ou tipo seja, e
por isso cabe numa checagem determinística sobre o documento montado — comparar
as duas grafias ignorando caixa e apontar quando a que foi escrita não é a que
aquela posição pede.

**Lado do banco (10/nov):** com a errata, o banco de 09/nov traz as duas formas
certas (N.19 e grafias da errata lá: chaves `notifyMOI…`, schemas `NotifyMoi…`).
O risco daqui continua sendo o generator trocar uma pela outra ao escrever. A
checagem não foi feita.

---

## P9 — `$ref` de resposta de erro apontando para o schema de sucesso

**Estado:** a origem foi eliminada no banco; a checagem daqui não foi iniciada.
(Não confundir com o "P9" do rulesbank, que é o conserto dessa mesma origem.)

**Lado do banco (10/nov):** a regra que produzia isto (o corpo de **sucesso** sem
código posto em `default`) foi corrigida lá (P9 de lá; D.1/N.25, 08/nov): corpo de
resultado sem código não gera regra, e corpo de erro sem código vai para `default`
com o tipo de erro. No banco de 09/nov, o PUT (162) não tem `default` de sucesso. A
checagem determinística abaixo continua útil como guarda: o Patcher ou a correção
podem produzir a mesma troca sem regra que a peça.
**Origem:** `openapi_rulesbank/docs/problems/02_Problems_ProvMnS_LEGACY_ate_20261107.txt` §3.19(c).

A resposta `default` do PUT saiu com `$ref` para o schema do próprio recurso,
onde o alvo tem o schema de erro do documento importado. É erro de conteúdo, não
de degradação: o endereço está certo e o valor aponta para outra coisa.

O que o torna detectável sem saber nada da spec: **a resposta de erro de uma
operação não referencia o mesmo schema que o corpo de sucesso dessa operação.**
São contratos diferentes por definição. É candidato do Reflector, determinístico.

## P10 — Relatório do Patcher e schemas vazam para dentro da operação (regressão?)

**Estado:** causa achada no código (10/nov). Consertado em parte em 12/nov: o
relatório do modelo (`applied`/`skipped`/`removed`) passa a ser removido em
qualquer profundidade (`_strip_report_keys`, que preserva chaves que são nomes,
como uma propriedade chamada `removed`), com aviso no log
(`tests/unit/test_correction_report_keys.py`). Falta o saneador da forma do
documento (decisão em aberto: recusar e registrar, ou mover).
**Origem:** `openapi_analysis` (avaliação do generator, camadas 1 e 2), 2026-10-06.
**Execução:** `data/outputs/test_pipeline/provmns_20260920_194130.yaml` — a rodada
de 20/set das **19:41**, posterior à de referência (`…184338`).

Defeitos que constam como **resolvidos** aqui (P4 e "Bugs do modo de correção do
Patcher") reaparecem nesta execução:

- **o relatório do modelo sobre o próprio trabalho vazou para o YAML.** Em
  `paths./{className}={id}.put.callbacks` há as chaves `applied` (lista de caminhos
  que o modelo diz ter alterado: `components.Resource`,
  `components.schemas.MoiChange`, `paths./{className}={id}.patch.requestBody.responses`…)
  e `removed`; em `paths./{className}={id}.put` há `skipped`, com o texto: *"The
  requested schema blocks are included under the path-item correction payload because
  the patcher response schema does not permit a separate root components block in the
  same correction object."* O filtro de `patcher.py` (~504-511,
  `k not in ("applied", "skipped", "removed")`) não pega esses níveis;
- **schemas fora de `components.schemas`.** O `skipped` acima explica: sem poder
  devolver `components` na raiz da correção, o modelo pôs os schemas dentro do
  fragmento da operação — há `components` em `put` e em `put.callbacks` (Resource,
  Scope, MoiChange, NotifyMoiChanges, PatchOperation, ErrorResponsePatch), um **path
  chamado `components`**, e `MoiChange` / `NotifyMoiAttributeValueChanges` direto em
  `components` (fora de `schemas`);
- **chaves que não são operação no path item:** `notifyMOIDeletion` e
  `$request.body#/notificationTarget` em `paths./{className}={id}`; um callback com o
  nome de uma expressão (`put.callbacks.'$request.body#/notificationTarget'`);
- **`responses` dentro do `requestBody`** do PATCH.

Medido pelo `openapi_analysis`: o documento **não é OpenAPI 3.0 válido** — 18
problemas (11 de estrutura contra o JSON Schema oficial do OpenAPI 3.0 e 7 `$ref`
internos quebrados: `MoiChange`, `AttributeValueChangeSet`, `ErrorResponsePatch` não
existem em `components.schemas`). Contrato 38/169 (22,5%), contra 62/169 da rodada de
28/ago. As duas rodadas anteriores avaliadas lá (`…232853` de 28/ago) são válidas.

O que verificar primeiro: o caminho de **correção** do Patcher — onde os campos de
relatório são filtrados (só no nível de cima?) e qual formato de resposta impede o
modelo de devolver `components` na raiz. Relatório completo:
`openapi_analysis/data/outputs/generator/ProvMnS/provmns_20260920_194130.txt`.

### Causa (leitura do código em 10/nov)

A execução das 19:41 é anterior ao commit `98d0fdc` (20:15), mas o código atual
**não fecha** nenhum dos caminhos vistos nela:

- **o filtro olha só o primeiro nível.** `content_only` (`patcher.py:508-512`) tira
  `applied`/`skipped`/`removed` das chaves diretas de `corrected.paths` e
  `corrected.components`. Um `applied` em `paths./x.put.callbacks` passa;
- **o merge aceita qualquer chave.** O `merge` da correção (`patcher.py:496-502`) e
  o `_deep_merge` do Assembler (`assembler.py:55-80`) põem no documento o que vier:
  um path chamado `components`, uma chave que não é operação no path item, um
  schema direto em `components`. O RASTREIO de 19/set já anotava que "a guarda no
  Assembler segue não implementada". O P4 foi fechado pelo prompt, não por código;
- **a revisão não vê esses casos.** `_structural_checks` (`reflector.py:77`) não
  confere a forma do documento. `_unreviewed_yaml` (`reflector.py:360-365`) só
  recolhe, dentro de um path válido, as chaves que são método HTTP, e de
  `components` só `schemas`. Então `notifyMOIDeletion` sob o path item, ou
  `components.MoiChange`, só chegam à segunda passagem, pelo documento inteiro, e
  nunca como candidato determinístico.

O "não permite um bloco `components` na raiz", no `skipped`, não corresponde ao
schema atual: `PatcherCorrectedDocument` tem `components` na raiz. Ou a resposta é
de um estado intermediário daquela sessão, ou o modelo leu mal o schema. Os
defeitos do YAML não dependem disso: com o código de hoje eles entram igual.

**Conserto genérico (vale para qualquer MnS):** um saneador da forma do documento,
aplicado em todo merge (Assembler e correção):
- chave de `paths` começa com `/`;
- um path item só aceita os métodos, `parameters`, `summary`, `description`,
  `servers` e `$ref`;
- `components` só aceita as chaves fixas da especificação;
- os campos de relatório são removidos em qualquer profundidade.

O que for recusado é registrado no log, nunca descartado em silêncio. Além disso, o
`openapi-spec-validator` como checagem estrutural do documento final (já previsto
no NODES.md), alimentando os candidatos do Reflector.

## P11–P16 — Mudanças do banco do rulesbank a conferir aqui (08/nov/2026)

**Estado:** a verificar. Nada foi rodado no gerador com elas.

**Atualizado em 10/nov** com o run de 09/nov do rulesbank (`04_Problems_ProvMnS.txt`
de lá) e a leitura do código daqui:

| | No banco (09/nov) | No generator (código de 10/nov) |
|---|---|---|
| P11 | OK (D.3): GET scope `form/true`, attributes e fields `form/false`, filter e dataNodeSelector sem nada; DELETE scope `form/true` | **não tratado**: nada no guia de tipos (`rule_types.py`) nem no prompt fala de `style`/`explode`. O valor chega como `array of string; style: form; explode: false`, e o Patcher tem de adivinhar que o sufixo vira dois campos do Parameter Object. Aberto aqui |
| P12 | OK (N.27): `Resource.additionalProperties` = `array of object` | o guia já descreve `additionalProperties`; medir |
| P13 | OK em 6 de 8 tipos (N.8); `PatchItem` e `Scope` ainda com required a mais (R3) | o generator escreve o que a regra diz; medir |
| P14 | OK (N.25): `default` com o tipo de erro, PATCH `oneOf`; mas N.32, N.33 e N.37 abertos (R3) | o guia já manda escrever `default`/`4XX` como estão. A métrica muda pela D.10 (ver "Não são defeitos") |
| P15 | OK (N.9): `{request.body#/notificationRecipientAddress}`, sem `$`, igual ao Rel-18 | a chave ainda tem um ponto, e a remoção na correção divide caminhos por ponto (P19) |
| P16 | campo presente; **vazio** em 09/nov. Há outro campo que o generator também não lê: `rules_never_completed` (8 entradas) | não lido; decidir junto com o P7 |
**Origem:** `openapi_rulesbank`, `docs/problems/03_Problems_ProvMnS.txt` (itens citados) e
`docs/RASTREIO.md`, 08/nov/2026. Todas mudam o que o banco escreve; um banco novo da ProvMnS
(run com `--aux` user_errata_28532.md) é o que vai mostrar o efeito aqui.

- **P11 — `style`/`explode` no valor do `query_parameter` (D.3 lá).** Quando a spec escreve a
  serialização, o valor passa a ser `<schema>; style: <s>; explode: <b>` (ex.: `array of string;
  style: form; explode: false` em `attributes`/`fields` do GET; `scope` com `form`/`true`). Conferir
  se o Patcher escreve `style` e `explode` no Parameter Object a partir do valor; sem isso
  `attributes`/`fields` saem com o padrão `explode: true`, que serializa diferente do que a spec pede
  ("comma-separated list").
- **P12 — o mapa do `Resource` (N.27 lá).** A linha `n/a | map(array(object))` passa a ser UMA regra
  `components/schemas/Resource · additionalProperties` com valor `array of object` (antes saía
  `properties.n/a` e era descartada). Conferir que vira `additionalProperties: {type: array, items:
  {type: object}}`.
- **P13 — `required` (N.8 lá).** O banco passa a pôr em `required` só os atributos M que a spec não
  torna condicionais; o `Resource` sai `[id]` (TS 32.158 7.6). Nada muda no contrato da regra; só o
  conteúdo. Conferir que o gerador não completa o `required` por conta própria.
- **P14 — corpo de erro sem código em `default` (D.1/N.25 lá).** O banco deixa de inventar 4XX/5XX/400
  para a linha `error` das tabelas IS e passa a dar `default` (PATCH: `oneOf` de ErrorResponseDefault e
  ErrorResponsePatch). Os 4XX/5XX das cláusulas de método continuam. Liga com a divergência
  `4XX`/`5XX` contra `default` anotada no RASTREIO ("O que ainda falta") e com o P9.
- **P15 — chave do callback (N.9 lá).** O banco passa a escrever `{request.body#/notificationRecipientAddress}`
  em vez de `{request.body#/notificationTarget}`. O P10 viu chaves `$request.body#/notificationTarget`
  vazando no path item; reconferir com o banco novo.
- **P16 — `metadata.unresolved_missing` (N.26 lá).** Campo novo nos metadados do banco: regras que o
  Validator pediu e o Extractor nunca criou (seção, pedido, origem). O gerador hoje lê só `target` e
  `source_document` dos metadados; decidir se usa essa lista (por exemplo, como lacuna declarada) ou a
  ignora.

Também ainda não medido aqui: o corte do documento passou a ser o `split_sections` do
`qdrant_3gpp_rag` (commit `b2afeb7`, 08/nov; N.29 do rulesbank). Na i20 o gerador ganha os 7 títulos
sem estilo que antes não lia (ex.: 12.1.1.4.1a.10, 12.1.1.2.6) e os títulos de nível 1.

---

## P17–P25 — Achados da leitura do código (10/nov/2026)

**Estado (12/nov):** P17, P18, P20 e P22 consertados, com teste; P23 consertado em
parte. Commits `e2b5697` (P18), `77f8fa2` (P20, P22) e `c1cac5a` (P17, P10 parcial,
P23 parcial), no branch `fix/quick-robustness`. P19, P21, P24 e P25 seguem abertos. Nada disso
foi medido numa execução real. Vêm de uma leitura do código inteiro
contra este documento e o RASTREIO. Os três marcados *(confirmado)* foram
reproduzidos num script (LangGraph real, `_rule_group_key` real). Os outros são
leitura de código. Todos são genéricos: nenhum depende de MnS.

Os 16 testes do repositório passam, mas cobrem só o parser de seções e a métrica.
Os pontos determinísticos onde nasceram os defeitos deste arquivo (P4, P5, os bugs
do modo de correção, P17, P19, P20, P22) não têm teste: `_rule_group_key`, os dois
merges, a remoção declarada, `_structural_checks` e `_unreviewed_yaml`. Os
notebooks de `tests/unit/` não rodam no pytest.

### P17 — O Validator ainda trunca o documento em 24 000 caracteres

**Consertado (12/nov):** o Validator recebe o documento inteiro
(`tests/unit/test_graph_end.py`).

`validator.py:30-36` (`_document_yaml(limit=24000)`). É o defeito que "Reflector
cego e truncado" dá como resolvido, mas o conserto só chegou ao Reflector. O
documento de 20/set tinha 26 756 caracteres, então o Validator escrevia consertos
sem ver o final dele. Com o P7 inflando os schemas, a parte cortada cresce. Também
não há aviso no log quando o corte acontece.

### P18 — Resposta estruturada `None` derruba a execução fora da correção

**Consertado (12/nov):** todas as 11 chamadas com saída estruturada (Planner ×3,
Reflector ×3, Validator, Patcher ×3, capa) passam por `utils/structured.py`
(`invoke_structured`), que lança `EmptyStructuredOutput` no `None`. Cada chamada cai
no `except` e no fallback que já tinha para uma chamada que falhou. A checagem
própria da correção saiu (`tests/unit/test_structured_none.py`).

O PROBLEMS registrou que `with_structured_output` pode devolver `None` em vez de
lançar, mas a proteção só existe em `_apply_corrections`. Nos outros pontos, o
`None` é usado direto e vira `AttributeError`:
- `reflector.py:540` (`final.issues`, fora do `try`);
- `validator.py:125` (`verdict.fixes`, fora do `try`);
- `planner.py:447`, `527` e `633` (`verdict.source_rule_ids`,
  `attach.attach_to_indices`).

No Planner isso derruba a execução antes do primeiro YAML.

### P19 — Caminho pontuado não sobrevive a uma chave com ponto *(confirmado)*

A remoção declarada (`patcher.py:532`) faz `path.split(".")`. A chave de callback
`{request.body#/notificationRecipientAddress}` (P15) se divide em
`{request` + `body#/…}`, então nenhuma remoção dentro de um callback resolve: só
sai o aviso "declared a removal of … which the document does not have". O `where`
do Reflector e do Validator usa a mesma notação e herda a mesma ambiguidade. O
conserto é uma notação que delimite as chaves, ou que resolva o caminho contra o
documento.

### P20 — A Phase B do Planner casa paths por substring *(confirmado)*

**Consertado (12/nov):** `_same_path` (`planner.py`) aceita o mesmo path, ou um que
termine nele (um prefixo de servidor que o LLM da Phase A tenha posto), e nunca um
path aninhado. Usado na Phase B e no Pass 1 (`tests/unit/test_planner_paths.py`).

`planner.py:566-569`: `op.path in raw_path or raw_path in op.path`. Um
`path_parameter` endereçado a `paths./alarms/{alarmId}` é anexado também às
operações de `/alarms`, que não têm essa variável. O `_rule_touches_op` do Pass 1
(`planner.py:265-268`) faz o mesmo. Não aparece na ProvMnS, que tem um path só, mas
aparece em FaultMnS, FileDataReporting e Streaming. O comentário justifica o
substring por um prefixo de servidor que não existe mais nos endereços do banco. O
conserto é usar igualdade.

### P21 — Regra anexada a uma operação `keep`/`discard` nunca é escrita

Com legado, o Pass 1 produz operações `keep`, e o Patcher as pula sem chamar o LLM
(`patcher.py:611`). Mas o fallback da Phase C (anexar à operação `[0]`), a Phase B2
(segurança em todas) e os `attach` das Phases A e C podem cair numa delas, e as
regras somem sem registro. Hoje está latente, porque as medições rodam sem legado.

### P22 — `probable_gaps` não chega ao estado *(confirmado)*

**Consertado (12/nov):** `extraction_plan` foi declarado em `OpenAPIGenState`, e o
Planner grava as regras não encaixadas em `x-ai-generation.unplaced-rules`, só
quando há alguma (`tests/unit/test_planner_paths.py`). Falta o `unresolved_missing`
e o `rules_never_completed` do banco no mesmo bloco (P16).

O Planner devolve `extraction_plan`, mas a chave não existe em `OpenAPIGenState`, e
o LangGraph a descarta: no estado final ela não existe. As regras que o Planner não
encaixou ficam só como contagem no log. É a "perda silenciosa" que o RASTREIO
descreve (19/ago), agora por outro caminho. O conserto é declarar o campo e levá-lo
ao bloco `x-ai-generation`, junto com o `unresolved_missing` e o
`rules_never_completed` do banco (P16).

### P23 — A coleção `spec_tmp_*` vaza fora da CLI

**Consertado em parte (12/nov):** o grafo termina num nó `finish` (Validator →
finish → END) que remove a coleção, então todo host que monta o grafo a remove
(`tests/unit/test_graph_end.py`). **Continua aberto:** uma execução que **lança**
nunca chega ao `finish`. E o `finally` do `main.py` também não cobria esse caso,
ao contrário do que o comentário dizia: com a exceção, `result` fica vazio e o nome
da coleção é desconhecido. Coleções `spec_tmp_*` órfãs de execuções que falharam
continuam possíveis, na CLI e fora dela.

O docstring do Loader diz que o Assembler remove a coleção. Quem remove é o
`finally` de `main.py:188-194`. O `openapi_chatbotUI` monta o grafo direto com
`build_openapi_gen_graph` e nunca a remove, então cada execução deixa uma coleção
no Qdrant. O fim do grafo é o Validator → END; a remoção pode ir para lá.

### P24 — O anexo pode entrar pelo corpus compartilhado (a verificar)

O `normative_only` protege só a coleção da execução. Planner, Patcher, Reflector e
o passo de `servers` consultam `3gpp_rel18_28` (`QDRANT_COLLECTION`) **sem filtro**
(`retriever.py:236`). Se esse corpus indexou a TS 28.532 com o Anexo A, uma busca
como "resource URI structure server root …" (passo de `servers`) pode trazer o
`servers:` do próprio alvo. O rulesbank resolveu o caso dele filtrando
`annex: False` nas buscas (N.30 lá), e o `openapi_analysis` marca o anexo no índice.
Falta conferir se o corpus daqui tem esse campo e passar o filtro.

### P25 — Documentação de apoio descreve o desenho antigo

- `README.md` diz que "todos os nós exceto o loader são stubs" e que os prompts
  estão "a preencher". Não mostra a CLI `main` nem o `eval`;
- `NODES.md` descreve stubs, retry por operação e constantes que não existem mais
  (`MAX_OP_ITERATIONS`, `VALIDATION_ERROR_THRESHOLD`);
- docstrings: `graph/state.py` e `__init__.py` ainda falam em retry por operação;
  o passo 5 do Patcher ("On retry") e o `_build_correction_task` são inalcançáveis,
  porque o modo de construção só roda com `validation_errors` vazio; os campos
  `reflected_fragment` e `validated_fragments_by_op` e o `ValidationVerdict` não
  têm consumidor; o Validator preenche `missing_rules: []` por herança; o Planner
  fala em "três destinos" e lista quatro;
- configuração: `MODEL` tem como padrão `gpt-4.1-mini` (as medições usam o luna); o
  Planner usa `with_structured_output` sem `method="function_calling"`, diferente
  dos outros nós;
- o RASTREIO, em "O que ainda falta", ainda diz que o Reflector e o Validator são
  stubs (são implementados desde 23/ago). Esse trecho fica como registro histórico,
  e a nota de 10/nov lá corrige.

---

## Não são defeitos (registrado para não reabrir)

**~~`4XX`/`5XX` no lugar de `default`~~ — substituído pela D.10 do rulesbank
(decidida em 09/nov).** Eu contava isso como convenção e pedia que a métrica
tratasse `4XX`/`5XX` como equivalente a `default`. A decisão de lá é mais fina e
vale aqui, aplicada **por operação**:

| A operação tem | Leitura |
|---|---|
| só `default` | certo |
| só `4XX` + `5XX` | melhoria (juntos fazem o papel do `default`) |
| `4XX` + `5XX` + `default` | os ranges estão certos; o `default` sobra e é errado (N.37) |
| `default` + um só range | o `default` está certo; o range sozinho é errado |

Os ranges só contam como certos com o tipo da tabela do método e um `$ref` válido.
A métrica em `eval/comparison.py` não aplica nada disso: compara folha a folha.
Para comparar com o `openapi_analysis`, que vai aplicar a D.10, ela precisa do
mesmo critério. O generator não deve descartar o `default` por conta própria: isso
cabe ao banco (N.37 lá).

**Regras que o banco não produziu.** Media types do PATCH (§3.2), respostas 200 e
204 do PATCH (§3.3), media types alternativos do GET 200 (§3.4), e as 15 ausentes
do §3.19. Sem regra não há de onde tirar, e supri-las no generator seria embutir
conhecimento de uma spec específica.
*Atualização (10/nov):* o princípio vale igual, mas a lacuna mudou de lugar. Desde
07/nov o rulesbank recebe esses fatos pela **errata** (entrada do usuário, `--aux`,
"tudo o que a spec não dá"). O banco de 09/nov já traz os media types do PATCH, o
200 e o 204 do PATCH e os três media types do GET 200. O generator continua sem
supri-los: eles chegam como regra.

**O `anyOf` de NRMs em `Resource`** (§3.10). Lacuna declarada e não-extraível: a
lista cresce a cada release e as specs de origem não são citadas pelo documento.
Decisão registrada do lado do banco de não embutir lista; vale aqui igual.
*Atualização (10/nov):* a errata passou a dar a composição (`oneOf [objeto, anyOf
dos 19 NRMs]`), mas o banco de 09/nov ainda não a produz (N.31, R3). Quando
produzir, chega como regra; o generator continua sem embutir lista.

---

## Resolvidos

### P6 — O laço de correção não convergia (2026-09-20)

Terminava sempre por limite de rodadas, com fixes pendentes (8 na execução de
19/set). Em 20/set fecha sozinho: rodada 1 → 1 fix, rodada 2 → 1 fix, rodada 3
→ `Validator → nothing to fix`.

A causa não era o revisor. Eram duas, e as duas foram corrigidas:

- o Patcher recriava o `components` aninhado a cada fragmento, e o laço gastava
  as rodadas limpando sujeira que o gerador reproduzia (ver P4);
- a chamada de correção pedia o **documento inteiro** de volta (ver abaixo), o
  que a tornava lenta o bastante para estourar o timeout.

### P5 — O laço de correção apagava uma operação inteira (2026-09-20)

Um `remove` apontado para uma folha apagava o ancestral, porque o merge
comparava caminhos com `startswith` sobre string e testava em cada nível da
descida: `"…put.responses.default".startswith("…put")` é verdadeiro, e o PUT
saía inteiro com seus 5 callbacks.

Corrigido trocando inferência por declaração: o merge **nunca remove nada**, e
o modelo declara em `removed` o caminho exato do que tirou; a remoção navega
os segmentos até o pai e tira só a última chave. Um caminho que não resolve é
logado, não silenciado.

Verificado em 20/set: o PUT sobrevive com os 5 callbacks, e nas execuções
intermediárias o aviso `declared a removal of … which the document does not
have` apareceu sem nada ser apagado.

### P4 — `components` aninhado dentro de `paths` (2026-09-20)

O Patcher devolvia o bloco `components` dentro do campo `paths` do fragmento —
e como o fragmento é mesclado em `final_openapi["paths"]`, o que deveria ser
irmão virava um path chamado `components`. Na pior execução eram **24 schemas
presos ali**, cópia inteira do documento, com os `$ref` apontando para o topo
e quebrando.

Piorou temporariamente quando o Patcher passou a receber o documento inteiro:
vendo um documento com `paths` e `components`, ele devolvia algo com a mesma
forma. A correção foi separar no prompt o que se **lê** do que se **devolve** —
o documento é contexto, o fragmento é só o que a operação acrescenta — e
enumerar as chaves permitidas em `paths`.

Verificado em 20/set: as 4 operações produziram `1 path block` cada, e `paths`
tem só `/{className}={id}`.

### A correção pedia o documento inteiro de volta (2026-09-20)

`PatcherCorrectedDocument` exigia *"the complete `paths` block"* e *"the
complete `components` block"*: para aplicar 7 consertos pontuais, o modelo
reescrevia ~20 000 caracteres, três vezes por execução.

É a origem de boa parte do que foi investigado nesta sessão. As defesas
espalhadas pelo código — mesclar chave a chave, tirar `applied`/`skipped` do
YAML, recusar remoções não pedidas — existiam todas para conter uma reescrita
que ninguém queria. O comentário dizia "o documento é editado, não gerado de
novo"; o schema pedia o contrário.

E quebrou de vez quando o documento cresceu: 3 tentativas de 60 s falharam,
`with_structured_output` devolveu `None` em vez de lançar, e `corrected.paths`
derrubou a execução inteira.

Corrigido em duas partes: o schema e o prompt passam a pedir **só os blocos
alterados** (o documento continua indo inteiro como contexto de leitura), e um
`None` passa a gastar a rodada em vez de matar a execução.

Medido: rodada de correção de ~5 min (timeout) para **2 s e 7 s**; execução de
US$ 0,17/476 s para **US$ 0,096/234 s**.

### Reflector cego e truncado (2026-09-20)

Duas retenções de informação, nos dois extremos da revisão:

- a segunda passagem truncava o documento em 24 000 caracteres, e o documento
  tinha 26 756 — os últimos 10% nunca eram revisados, em silêncio;
- a primeira passagem montava fatias pelo plano (`paths[path][method]`), então
  **nada fora de uma operação planejada chegava à revisão**: um `components`
  aninhado, um método solto no nível de `paths`, nenhum deles existia para o
  revisor.

Corrigido: a segunda passagem recebe o documento inteiro, e uma passagem nova
cobre tudo que as fatias não alcançaram. Em 20/set ela encontrou 4 problemas
que antes eram invisíveis, e os candidatos determinísticos saltaram de 1 para
51 — não porque as checagens mudaram, mas porque passaram a varrer o documento
todo.

### Parâmetros duplicados entre path e operações (2026-09-20)

Os 7 parâmetros apareciam no nível do path **e** repetidos em GET, PATCH e
DELETE. A duplicação deslizava os índices e estragava a comparação.

A causa era o Patcher escrever às cegas: recebia apenas uma lista de nomes de
schemas (`"Resource, Scope, …"`) e nada da estrutura, então cada chamada
reescrevia os parâmetros do path dentro da sua própria operação. Corrigido
dando-lhe o documento construído até ali — ~6,7k tokens sobre os ~10k que o
prompt já usava, contra menos de 1k de economia que a omissão trazia.

Verificado em 20/set: os 7 no path, zero repetições nas operações.

### A capa lida do texto errado (2026-09-20)

Ao cortar o anexo na leitura (`normative_only`), passei a dar à chamada que lê
a capa o texto já reduzido — e a capa fica **acima** do sumário, que é o
primeiro corte. O documento saiu sem `version`, sem `copyright` e sem
`externalDocs`.

Corrigido separando as duas leituras: a capa vem do documento como está, o
resto do texto normativo. Metadata voltou de 43% para 71%.

### O anexo entrava no "texto normativo" (2026-09-20)

`_normative_text` cortava na **última** ocorrência de "Annex" — mas o sumário
lista os anexos antes do corpo, e o documento tem Anexos A, B e C. O corte caía
no Anexo C, deixando A e B dentro do texto dito normativo. O Anexo A é o YAML
alvo: 610 mil caracteres a mais, incluindo a resposta que o pipeline existe
para derivar.

Afetava a dedução dos `servers` tanto quanto a leitura do título. Corrigido
cortando o sumário primeiro e os anexos no primeiro cabeçalho restante,
aplicado uma vez na leitura do documento.

### P1 — A Phase C do Planner descartava regras de schema (2026-09-19)

**A causa dominante.** A Phase C pergunta a um LLM quais operações usam cada
schema, e tratava "nenhuma operação usa" como motivo para descartar a regra. Com
4 operações e 16 schemas a cadeia operação→schema→schema ficou longa demais e 49
das 122 regras caíram, levando 11 dos 16 schemas do alvo com elas.

Correção em `planner.py:613-630`: uma regra que define um schema é a autoridade de
que o schema pertence ao documento; qual operação o referencia é outra pergunta, e
responder "nenhuma" não autoriza descartar a definição. Um schema não reclamado é
anexado à primeira operação, que apenas o carrega.

Medido: contrato **17% → 46%**, regras cobertas 97/122 → **121/122**, schemas
5/16 → **16/16**. Mais barato (US$ 0,166 → 0,143) e mais rápido (537s → 358s).

### P2 — `$ref` para schemas não definidos (2026-09-19) — *fechado como métrica*

De 16 para **0** no contador, por duas vias, e só uma era a prevista:

- schemas que o banco define voltaram por P1— este é o conserto real;
- tipos importados **saíram do contador sem serem resolvidos**: o Patcher lhes
  deu definição local. Nenhuma referência aponta para o vazio, então o contador
  fica verde, mas o documento tem 28 schemas onde o alvo tem 16.

Ou seja: **a métrica fechou, o problema mudou de lugar** — está em P7. Registrado
aqui como advertência: contar `$ref` quebrados não distingue "a referência foi
resolvida" de "o alvo foi inventado".

### Bugs do modo de correção do Patcher (2026-08-23)

Três defeitos determinísticos que faziam a revisão estragar um documento bom,
corrigidos e verificados no PerfMnS:

- a correção substituía blocos inteiros (`updated["components"] = ...`), apagando
  schemas quando o modelo devolvia só o que tocou;
- `applied` e `skipped` — o relatório do modelo sobre o próprio trabalho —
  vazavam para dentro do YAML publicado;
- o conjunto `covered` era montado por regex sobre o YAML e capturava a chave
  `schemas` em vez dos nomes, fazendo os schemas alcançáveis serem revisados uma
  segunda vez fora do contexto da operação.

### Reasoning desligado em vez de omitido (2026-09-15)

`REASONING_EFFORT=none` enviava `{"reasoning": {"enabled": False}}` ao
OpenRouter. O rulesbank mediu que o flag de desativação dá respostas piores do
que omitir o parâmetro — 8 execuções de cada, temperatura 0. Agora `none` omite.
