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

Não é versionado (a pasta `docs/` está no `.gitignore`).

Última atualização: 2026-09-20 (correção por blocos, contexto do documento).

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

**Estado:** aberto. **É defeito do `openapi_rulesbank`, não do generator.**
**Impacto:** 18 das 28 folhas de contrato perdidas em 20/set.

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
leitura**, o mesmo padrão que o `Problems_ProvMnS.txt` §3.19 registrou para o
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

---

## P7 — Tipo importado ganha definição local

**Estado:** aberto. **O maior dos que restam.** Em 20/set: 23 schemas contra os
16 do alvo — os 7 a mais são tipos importados com definição inventada.
**Origem:** `openapi_rulesbank/Problems_ProvMnS.txt` §3.6 e §3.19(a).

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

---

## P8 — Convenção de nomes entre chave de callback e nome de schema

**Estado:** aberto, não iniciado.
**Origem:** `openapi_rulesbank/Problems_ProvMnS.txt` §3.7, verificado lá contra
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

---

## P9 — `$ref` de resposta de erro apontando para o schema de sucesso

**Estado:** aberto, não iniciado.
**Origem:** `openapi_rulesbank/Problems_ProvMnS.txt` §3.19(c).

A resposta `default` do PUT saiu com `$ref` para o schema do próprio recurso,
onde o alvo tem o schema de erro do documento importado. É erro de conteúdo, não
de degradação: o endereço está certo e o valor aponta para outra coisa.

O que o torna detectável sem saber nada da spec: **a resposta de erro de uma
operação não referencia o mesmo schema que o corpo de sucesso dessa operação.**
São contratos diferentes por definição. É candidato do Reflector, determinístico.

---

## Não são defeitos (registrado para não reabrir)

**`4XX`/`5XX` no lugar de `default`.** Eu vinha contando isso como divergência
nas comparações de PerfMnS e ProvMnS. O rulesbank registra explicitamente que é
convenção adotada, vinda da referência OpenAPI, e mede cobertura tratando um
`default` do alvo como coberto quando o gerado emitiu `4XX`/`5XX`. **A métrica de
contrato em `eval/comparison.py` deveria fazer o mesmo** — enquanto não fizer, o
número de contrato está subestimado por um motivo que não é erro.

**Regras que o banco não produziu.** Media types do PATCH (§3.2), respostas 200 e
204 do PATCH (§3.3), media types alternativos do GET 200 (§3.4), e as 15 ausentes
do §3.19. Sem regra não há de onde tirar, e supri-las no generator seria embutir
conhecimento de uma spec específica.

**O `anyOf` de NRMs em `Resource`** (§3.10). Lacuna declarada e não-extraível: a
lista cresce a cada release e as specs de origem não são citadas pelo documento.
Decisão registrada do lado do banco de não embutir lista; vale aqui igual.

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
