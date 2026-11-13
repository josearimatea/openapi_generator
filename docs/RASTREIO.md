# Documento de Rastreio — OpenAPI Generator

Este documento é um caderno de bordo do projeto, no mesmo espírito do `RASTREIO.md`
do `openapi_rulesbank`. A ideia é registrar, em texto corrido, as ideias que motivaram
cada etapa, o que foi de fato implementado, o que mudou e por quê, e o que foi testado —
de forma que sirva como matéria-prima para a escrita da dissertação mais adiante. Não
pretende ser documentação técnica (isso está no README, no NODES.md e no código);
pretende contar a *história* das decisões.

É versionado desde o commit `c16ce9b`; antes a pasta `docs/` ficava no `.gitignore`,
por ser um registro pessoal e em evolução. A cada mudança relevante, acrescente um
parágrafo novo em vez de reescrever o que já está aqui.

Registro de atualizações:
- 2026-08-19 — correção da Phase C, achado do `requestBody` inferido.
- 2026-08-22 — métrica de comparação versionada, teste entre modelos, taxonomia dos
  oito tipos de regra.
- 2026-08-23 — `info`/`externalDocs` lidos da capa e `servers` deduzido das cláusulas
  de URI, ambos por LLM; proveniência agrupada e apresentação alinhada ao original.
- 2026-08-23 — Reflector e Validator implementados, topologia com o Assembler antes da
  revisão, e os três bugs que faziam o revisor estragar o documento.
- 2026-09-19 — o ProvMnS em escala: a Phase C deixa de descartar schemas (17% → 46% de
  contrato), e o laço de correção aparece como o defeito dominante que sobrou.
- 2026-09-20 — a sessão de esconder informação: o Patcher, o Reflector e a chamada de
  correção trocam fragmentos por documentos e documentos por fragmentos; o laço
  converge pela primeira vez.
- 2026-10-01 — seis divergências entre o rulesbank e o gerador, fechadas do lado do
  gerador; o nível dos parâmetros passa a ser decidido pelo endereço da regra.
- 2026-10-06 — P10: o `openapi_analysis` acha relatório do Patcher e schemas vazando
  para o YAML de 20/set 19:41 (registrado só no `PROBLEMS.md`).
- 2026-11-08 — o corte do documento passa a ser o do `qdrant_3gpp_rag`; seis mudanças
  do banco a conferir aqui (P11–P16).
- 2026-11-10 — leitura do código inteiro contra os documentos, e alinhamento com as
  decisões do rulesbank até o run de 09/nov.

---

## A ideia central

Se o `openapi_rulesbank` transforma a especificação normativa do 3GPP num banco de
regras estruturado, o `openapi_generator` é a etapa seguinte: transformar esse banco de
regras numa OpenAPI concreta. A hipótese de trabalho é que, tendo as regras já extraídas
e validadas, a geração do documento deixa de ser um problema de leitura de spec e passa a
ser um problema de composição — montar cada operação e cada schema a partir das regras
que os fundamentam, de forma rastreável.

O pipeline é um grafo LangGraph com um laço por operação: um Loader carrega os insumos,
um Planner decide quais operações gerar e quais regras fundamentam cada uma, e então,
para cada operação, um Patcher produz o fragmento OpenAPI, um Reflector critica, um
Validator decide se aceita ou manda refazer, e um Assembler funde o fragmento no
documento acumulado. A escolha de trabalhar operação por operação, e não documento
inteiro de uma vez, tem a mesma motivação que a decisão análoga no rulesbank: mantém os
erros localizados e permite refazer só o que falhou.

Uma premissa importante do desenho, que vale registrar porque tem consequências: o banco
de regras é o **contrato exclusivo** da geração. A spec 3GPP entra apenas como contexto
de apoio via RAG, e o prompt do Patcher proíbe explicitamente usá-la como fonte de
construções que nenhuma regra pediu. Isso torna a geração auditável — cada elemento do
YAML deveria poder ser rastreado até uma regra — mas significa também que uma lacuna no
banco vira uma lacuna no documento final, sem que o gerador tenha como compensar.

---

## O problema das regras que o banco rejeitou, e o *reasoning* sobre elas

Esta é a decisão de projeto mais interessante até agora, e a que gerou o teste descrito
mais adiante.

O `openapi_rulesbank` tem um laço de validação por seção com um número máximo de
tentativas. Quando esse limite se esgota e ainda restam regras com erro *semântico*, o
Builder não as descarta: ele as inclui assim mesmo no banco, marcadas com
`validation_passed: false` e com uma `validation_notes` que registra a objeção do
validador ("Force-included at max attempts. Failed: ..."). A intenção original era não
perder informação silenciosamente.

Isso cria um problema para o gerador. Na primeira versão, o Patcher lia
`rules_bank["rules"]` e montava o prompt com cinco campos por regra — tipo, nome, texto,
objeto e valor — e **nenhum** dos campos de qualidade. Uma regra que o validador tinha
rejeitado chegava ao LLM exatamente igual a uma regra aprovada. O modelo não tinha como
distinguir, e o pipeline não tinha nenhum ponto onde a decisão "isso entra ou não no
YAML?" fosse tomada. Tudo entrava.

A primeira ideia foi a mais óbvia: filtrar por `validation_passed`. Foi descartada na
discussão, e por um bom motivo — jogar fora tudo que o validador rejeitou trata o
validador como infalível, e ele não é. A ideia que substituiu essa é a que está
implementada hoje: quando o validador rejeitou uma regra, quem desempata é a avaliação
do **Reflector** sobre ela. O Reflector é o nó do rulesbank que faz a auto-crítica por
regra, atribuindo confiança e sinalizando dúvida; se ele defende a regra, ela merece ser
julgada, não descartada.

O gate ficou assim, em `nodes/patcher.py::_rule_verdict`:

- `validation_passed: true` → verdict `ok`. A regra é autoritativa, nada a decidir.
- rejeitada, mas o Reflector a defende (não sinalizou dúvida, ou pontuou acima do
  limiar `RULE_RESCUE_CONFIDENCE`) → verdict `review`. A regra **entra no prompt**,
  marcada como `DISPUTED`, acompanhada da objeção do validador e do raciocínio do
  Reflector. O LLM do Patcher arbitra com a mesma evidência que um humano teria.
- rejeitada e o Reflector também duvidava (`discard_suggestion`, ou sinalizada *e*
  pontuada abaixo do limiar) → verdict `drop`. Nunca chega ao prompt.

Dois detalhes de implementação que valem registro. O primeiro: o rulesbank não persiste
o `error_type` numa regra força-incluída — ele o dissolve no texto do `validation_notes`.
O gate portanto extrai a objeção do texto após "Failed:", o que funciona mas é frágil;
persistir `error_type` como campo próprio no rulesbank seria mais limpo. O segundo: o
`reflection_confidence` vem `null` em todas as regras dos bancos gerados com
`gpt-5.6-luna`. O gate trata ausência de score como "não pontuado", nunca como zero — se
tratasse como zero, um limiar numérico ingênuo descartaria o banco inteiro. Esse caso
apareceu na prática e teria passado despercebido sem o teste.

O limiar `RULE_RESCUE_CONFIDENCE` mora em `config/settings.py`, com override por
variável de ambiente, seguindo a convenção do projeto de não deixar constante mágica
espalhada pelos nós.

---

## O teste que mostra o gate funcionando

O teste aproveita uma circunstância feliz: existem dois bancos de regras para o mesmo
serviço (PerfMnS, a Performance Threshold Monitoring MnS da TS 28.532), gerados com
poucos minutos de diferença, um com regras rejeitadas e outro sem. Isso permite comparar
o comportamento do gerador com e sem regras disputadas, mantendo todo o resto igual.

- `rules_bank_..._20260818_214526.json` — 17 regras, **todas** validadas.
- `rules_bank_..._20260818_210723.json` — 17 regras, **duas** rejeitadas pelo validador.

O *ground truth* é o `TS28532_PerfMnS.yaml` oficial: um path (`/notificationSink` com
`post`) e quatro schemas (`NotifyThresholdCrossing`, `PerfMetricValue`,
`PerfMetricDirection`, `PerfNotificationTypes`).

A execução foi feita **sem YAML legado** — só regras e spec. Isso é essencial: numa
execução anterior, com o legado Rel-17 como entrada, o Planner concluiu (corretamente,
já que Rel-17 e Rel-18 do PerfMnS são semanticamente idênticos) que bastava manter o
legado, marcou a operação como `keep`, e o Patcher pulou a chamada ao LLM inteira. O
resultado bateu 31 de 32 folhas com o alvo, mas **não media geração nenhuma** — era uma
cópia do legado. Fica o registro como armadilha metodológica: um placar alto pode
significar que o pipeline não fez nada.

As duas regras rejeitadas no banco de 21:07 são, e isto é o mais revelador, **mutuamente
contraditórias**. O validador do rulesbank rejeitou a regra `callback` dizendo *"Discard —
esta seção só define POST /notificationSink; não estabelece um callback anchor. Emita um
callback, se aplicável, a partir da subscription"*, e rejeitou a regra `path_operation`
dizendo *"Não mapeie /notificationSink como path operation. Converta esta notificação em
um callback"*. Ou seja: mandou converter path em callback **e** callback em path, na
mesma seção. Nenhuma das duas podia passar, e ambas foram força-incluídas ao esgotar as
tentativas.

O *ground truth* desempata a favor do `path_operation`: o PerfMnS é um servidor no lado
do consumidor (`servers: {root}`, descrito na spec como *"the open API server ... is
located in the consumer side"*), e `/notificationSink` é um path real.

**Resultado.** A regra `path_operation` (índice 1) foi classificada pelo gate como
`review`, entrou no prompt marcada como `DISPUTED` com objeção e defesa, e o LLM decidiu
a favor dela — produzindo as linhas `/notificationSink:` e `post:` no YAML final, que é
exatamente o que o alvo tem. O gate fez o que se esperava: não confiou cegamente no
validador, mas também não ignorou a objeção; converteu a divergência entre dois agentes
numa decisão informada.

A regra `callback` (índice 0) não entrou — mas, e isto é importante para não superestimar
o resultado, **não foi o gate que a barrou**. Ela nunca chegou lá: o `_rule_group_key` do
Planner não reconhece `rule_type: "callback"` e devolve `None`, então a regra foi para o
balde de "não classificadas" e virou gap. O desfecho foi o desejável por acidente, não
por avaliação de mérito. Uma regra `callback` legítima teria o mesmo destino.

---

## O que o teste revelou sobre o Planner (e que não era o alvo do teste)

O teste com o banco "sujo" acabou expondo um problema mais grave que o das regras
disputadas, e num lugar inesperado. O placar de folhas idênticas ao alvo caiu de 16/32
(banco limpo) para 12/32 (banco com erros), mas a causa não foi o gate.

O log do Planner mostra:

```
Phase C schema=PerfMetricValue        → no op uses it; 1 rule(s) flagged as gaps
Phase C schema=PerfNotificationTypes  → no op uses it; 1 rule(s) flagged as gaps
Phase C schema=PerfMetricDirection    → no op uses it; 1 rule(s) flagged as gaps
rules covered: 13/17; gaps: 7
```

Quatro regras nunca chegaram ao Patcher — a `callback` e três regras de schema. As três
de schema têm `validation_passed: true`: o gate as teria aprovado sem hesitar, mas elas
morreram antes. A Phase C do Planner é uma chamada de LLM que decide quais operações usam
cada schema, e aqui ela concluiu que nenhuma operação usava aqueles três.

A consequência no YAML foi direta: o Patcher recebeu `properties.observedPerfMetricValue:
$ref PerfMetricValue` sem nenhuma regra dizendo o que `PerfMetricValue` é, e preencheu com
`type: object` para não deixar referência quebrada. `PerfMetricDirection` perdeu o
`enum: [UP, DOWN]` e virou um `type: string` vazio. `PerfNotificationTypes` sumiu.

A primeira hipótese foi que a decisão da Phase C "oscilou entre execuções", já que com o
banco limpo ela anexara os mesmos três schemas normalmente. Um teste isolado desmentiu
isso e o diagnóstico correto é bem mais interessante: rodando a mesma Phase C, com a mesma
regra e o mesmo banco, variando **apenas o modelo**, o `gpt-4.1-mini` responde
`attach_to_indices=[0]` e o `gpt-5.6-luna` responde `[]`, de forma consistente. Não é
instabilidade — é um defeito determinístico do prompt que estava mascarado por um modelo
mais permissivo.

O defeito é que a Phase C pergunta "quais operações usam este schema?" mostrando apenas a
lista de operações do plano (`- [0] create POST /notificationSink (rules so far: 4)`) e
nada sobre os schemas que cada operação já carrega. Ora, um tipo raramente é referenciado
pela operação diretamente: `PerfMetricDirection` é alcançado através de
`NotifyThresholdCrossing`, que é o `requestBody` da operação. Sem enxergar essa cadeia, a
resposta honesta para "a operação usa `PerfMetricDirection`?" é "não" — e o luna, sendo
mais rigoroso, respondeu exatamente isso. O modelo antigo acertava por generosidade, não
por ter mais informação.

A correção foi tornar a cadeia visível: `_format_existing_plan` passou a aceitar as regras
e a listar, sob cada operação, os schemas que as regras já anexadas definem (usando o
mesmo `_rule_group_key` que o Planner já emprega para agrupar tudo — nada aí conhece
serviço ou schema específico). O prompt passou a instruir que alcançabilidade em qualquer
profundidade conta, e a avisar que devolver lista vazia **descarta** as regras.

Vale registrar que a correção tornou desnecessário o nó de schemas órfãos que se havia
planejado. A intuição inicial — "existe uma categoria de schema que nenhuma operação
alcança, e ela precisa de uma rota própria até o documento" — estava errada para este
caso: os três schemas nunca foram órfãos, eram alcançáveis e o prompt é que escondia o
caminho. Um nó extra ali seria uma segunda chamada de LLM por documento para consertar o
sintoma de uma pergunta mal formulada. Fica em aberto se algum banco futuro terá um schema
genuinamente inalcançável; se tiver, o nó volta a fazer sentido.

Fica também a lição mais geral: `probable_gaps` não é diagnóstico opcional, é perda
silenciosa de conteúdo. O pipeline seguiu até o fim reportando sucesso com sete regras
perdidas, e nada no relatório final distinguia isso de uma execução íntegra.

---

## O `requestBody` que ninguém pediu — inferência versus rastreabilidade

Este é, até agora, o achado mais importante do projeto para a discussão da dissertação, e
merece ser contado com cuidado porque é sutil.

O YAML oficial (Annex A da TS 28.532 Rel-18) define, no `POST /notificationSink`, um
`requestBody` obrigatório cujo conteúdo `application/json` referencia
`#/components/schemas/NotifyThresholdCrossing`. O banco de regras **não tem nenhuma regra
de `request_body`** — os tipos presentes são apenas `path_operation`, `response` e
`schema_property`, e nenhuma regra sequer menciona a expressão "request body" no texto.
Sabemos por que: no rulesbank, a regra correspondente foi extraída durante o processamento
da seção 562 (attempt 2), foi candidata em todas as tentativas e nunca foi descartada
explicitamente — ela simplesmente evaporou no laço de iterações, sem aparecer nem como
válida nem como falha. Por isso as 17 regras finais estão todas com
`validation_passed: true` e o banco *parece* íntegro: o defeito não é uma regra ruim, é
uma regra ausente, e ausência não aparece em nenhuma contagem.

E ainda assim o gerador produziu o `requestBody` — correto, com o `$ref` certo, batendo com
o Annex A. Nas duas execuções, com o banco limpo e com o banco defeituoso. É comportamento
consistente, não sorte pontual. Verificou-se também que o RAG da spec 3GPP não trouxe
nenhum trecho contendo `requestBody` naquela consulta, então a informação não veio de lá.

A explicação é a convergência de três coisas, nenhuma das quais é uma regra. Primeiro, o
próprio prompt do Patcher descreve o formato de saída dizendo que o objeto de operação
"includes summary, description, parameters, requestBody (when applicable), responses...":
a estrutura foi pedida, restando ao modelo julgar o "when applicable". Segundo, as três
regras de resposta descrevem o resultado de um POST de notificação que retorna
`204 No Content` — um POST assim praticamente exige um corpo de entrada. Terceiro, as nove
regras de schema definem `NotifyThresholdCrossing` por inteiro, e ele é o único schema de
topo que não é enum nem tipo simples, portanto o alvo óbvio do `$ref`. O modelo tinha o
formato, a motivação e o objeto, e completou o padrão.

O incômodo é que isso **viola explicitamente o próprio prompt**, cuja seção de grounding
manda que todo parâmetro, resposta, request body e propriedade de schema seja justificado
por uma regra, e diz literalmente "Do not invent fields". Há uma contradição interna no
prompt: ele descreve `requestBody` como parte do formato esperado e ao mesmo tempo proíbe
emitir o que nenhuma regra pediu. O LLM resolveu a contradição a favor do formato — e
acertou.

Por que isso importa para a tese. A leitura otimista é robustez: o gerador compensou uma
falha do estágio anterior, e o produto final ficou mais próximo do alvo do que o banco de
regras permitiria. A leitura pessimista é que o elemento não tem procedência — nenhuma
regra o justifica, ele não é auditável, e num pipeline cujo argumento central é a
rastreabilidade regra→artefato isso é exatamente o que não deveria acontecer. As duas
leituras são verdadeiras ao mesmo tempo, e é essa tensão que vale discutir.

Vale registrar, no entanto, que a leitura otimista é a mais forte das duas, e que o
enquadramento inicial deste caso como "problema" era pessimista demais. O que o fluxo
demonstrou é **capacidade de inferência sobre partes essenciais ausentes do banco**: com
apenas o formato pedido no prompt, três regras de resposta e nove de schema, o gerador
deduziu que um POST de notificação precisa de corpo, que o corpo é `application/json`, e
que o tipo é o único schema de topo não-enumerado — chegando ao mesmo `$ref` do Annex A.
Não foi acerto casual: repetiu-se nas duas execuções, com bancos diferentes. Isso é uma
propriedade desejável de um pipeline de múltiplos agentes, e é o que separa um gerador de
um preenchedor de template: o banco de regras é incompleto por natureza (nenhuma extração
automática de um documento normativo será exaustiva), e um sistema que produzisse
literalmente só o que as regras dizem geraria documentos inválidos sempre que faltasse uma
peça estrutural. A tensão com a rastreabilidade permanece real, mas ela é o preço de uma
robustez que se quer ter, não um defeito a eliminar.

Há ainda uma consequência metodológica que considero a mais séria: **o gerador mascara
lacunas do banco de regras**. Quem avaliar apenas o YAML final vai encontrar o
`requestBody` no lugar certo e concluir que o banco estava completo. A ausência da regra só
é visível confrontando o banco diretamente com o alvo — que foi como ela apareceu. Isso
significa que a qualidade do rulesbank não pode ser medida pela qualidade do YAML gerado:
são coisas diferentes, e o segundo esconde defeitos do primeiro.

E o risco é assimétrico. Aqui a inferência acertou. Se tivesse errado — escolhido o schema
errado, ou inventado um corpo numa operação que não deve ter nenhum — nada no pipeline
perceberia, porque o Validator do gerador é stub. Um elemento inferido corretamente e um
inferido incorretamente são hoje indistinguíveis no artefato final.

A ideia discutida para lidar com isso, ainda não implementada, é **tornar a inferência
visível em vez de proibi-la**: marcar no documento (ou num relatório paralelo) quais
elementos têm regra de origem e quais foram inferidos, permitindo dizer "destes N
elementos, M são rastreáveis e K foram inferidos". A alternativa — endurecer o prompt para
nunca emitir o que não tem regra — aumentaria a rastreabilidade, mas neste caso produziria
um documento *pior* que o atual, sem o corpo de requisição que o alvo tem. Preferiu-se não
apagar a diferença, e sim medi-la.

---

## Uma assimetria que vale para a dissertação

Investigando por que a regra `path_operation` importava, apareceu um argumento que
generaliza. As regras de `response` do banco têm `openapi_object` igual a
`paths./notificationSink.post.responses` — ou seja, **carregam o path e o método
embutidos na string**. O `_rule_group_key` do Planner extrai isso por regex, e por isso
cada regra de resposta, sozinha, já produz a chave `op:/notificationSink:post`.

A consequência é que descartar a regra `path_operation` não faria o path desaparecer: as
regras de resposta o recriariam. O que se perderia é a *semântica* (a regra
`path_operation` é a única que diz que aquele endpoint **é** a notificação
`notifyThresholdCrossing`, e é dela que sai o `operationId`), a *procedência* (o path
passaria a existir como subproduto de um regex sobre regras que falam de outra coisa), e
o *registro da disputa* (a objeção "isso deveria ser callback" só existe naquela regra).

Disso decorre uma assimetria com consequência prática: descartar uma `path_operation`
**correta** perde semântica mas não quebra o documento; descartar uma `path_operation`
**incorreta** não impede o erro, porque as regras de resposta recriam o path de qualquer
forma. Ou seja, para esse tipo de regra, descartar nunca é uma proteção eficaz — só perde
informação. Manter e marcar como disputada, que é o que o gate faz, é a decisão certa por
construção, e não por sorte neste caso.

---

## Os números depois das correções, e o que eles dizem

Com a Phase C corrigida, a query do RAG de referência derivada das regras, o prompt do
`$ref` e o `gpt-5.6-luna`, os dois bancos foram executados de novo, sem legado. O placar
usado (folhas do YAML idênticas às do alvo) evoluiu assim:

| | banco com erros (21:07) | banco limpo (21:45) |
|---|---|---|
| antes das correções | 12/32 | 16/32 (com `gpt-4.1-mini`) |
| depois | **18/32** | **18/32** |

Os dois **convergiram**. Um banco com duas regras rejeitadas e mutuamente contraditórias
passou a produzir o mesmo resultado de um banco impecável. Isso sugere que, neste caso, o
gargalo estava no gerador e não na extração — e é um argumento de robustez: o pipeline
tolera defeitos no banco de regras, desde que o gate arbitre as disputadas e o Planner não
perca as válidas pelo caminho.

Vale desconfiar do número, no entanto. A métrica achata o documento em folhas e conta prosa
igual a contrato: a `description` de uma resposta pesa tanto quanto um `enum`. Como as
descrições geradas são semanticamente corretas mas com outras palavras, contam como erro.
Removendo `description`, `summary` e `operationId` e comparando só a estrutura, o quadro é
bem melhor: `PerfMetricValue`, `PerfMetricDirection` e `PerfNotificationTypes` saem
**idênticos** ao alvo, as sete propriedades de `NotifyThresholdCrossing` saem idênticas, o
`requestBody` sai idêntico, e o path e o método também. A única diferença estrutural é a
lista `required`, que o gerado tem e o alvo não — e que existe porque a regra [12] a pede,
derivada da tabela do spec que marca aqueles seis campos como mandatórios. Ou seja, nesse
ponto o gerado é *mais* completo que o alvo, não errado.

Das 14 folhas que faltam, sete são `servers`, `externalDocs`, `info.description` e
`info.version` — blocos que nenhuma regra cobre; duas são a diferença `default` versus
`4XX`/`5XX`, em que o gerador seguiu as regras fielmente; e cinco são redação de
`description`/`summary`/`title` mais o `openapi: 3.0.3` contra `3.0.1`. Nenhuma é
propriedade ou tipo errado. Fica a lição de que a métrica serve para comparar execuções
entre si (foi útil para ver 12→18), mas é ruim como medida absoluta de qualidade; uma
métrica melhor separaria contrato, metadata e prosa em três números.

O tempo de ponta a ponta ficou em torno de **22 s** nas duas execuções (21,5 s a 22,7 s),
para uma operação e 17 regras.

---

## Outras mudanças desta rodada

**Estrutura de entradas.** As pastas `data/inputs/legacy/` e `data/inputs/rules/` passaram
a ser organizadas por MnS (`PerfMnS/`, `ProvMnS/`), porque o TS 28.532 define vários
serviços de gerenciamento no mesmo documento e misturá-los foi a origem de um problema
sério no ProvMnS: das 240 regras daquele banco, só 32% eram do serviço-alvo, e o gerador
produziu paths de Fault Supervision (`/alarms`), Streaming (`/connections`) e File Data
Reporting (`/files`) num documento que deveria ter apenas `/{className}={id}`.

**Query do RAG de referência OpenAPI.** Havia um erro conceitual: as duas coleções (a spec
3GPP e a referência OpenAPI) recebiam a *mesma* query, montada com vocabulário 3GPP
("POST /notificationSink — notifyThresholdCrossing"). Isso funciona bem para a primeira,
mas a coleção da referência OpenAPI não contém essas palavras em lugar nenhum — a busca
retornava trechos sobre Callback Object e Example Object, irrelevantes. O Patcher escrevia
`$ref` sem nunca ter recebido a definição do Reference Object. A correção foi derivar a
query da referência a partir das próprias regras: cada regra indica qual construção vai
produzir (`$ref` no valor → Reference Object; `allOf`/`oneOf` → Schema Object composition;
`rule_type: response` → Responses Object), e os nomes dessas construções são exatamente os
títulos das seções do documento de referência. Junto com isso, o `k` da recuperação subiu
de 5 para 10, porque três construções disputando cinco vagas se espremiam — verificado:
com `k=5` a cláusula normativa do Reference Object não chegava, com `k=10` chega.

**O caso do `$ref` com chave irmã.** A primeira geração produziu sete propriedades no
formato `{$ref: ..., description: ...}`. Consultando a referência OpenAPI, a spec é
explícita: o Reference Object *"cannot be extended with additional properties and any
properties added SHALL be ignored"*. Ou seja, a descrição era texto morto. Vale registrar
que a forma conforme de anexar algo a uma referência é envolvê-la em `allOf` (o objeto
externo passa a ser um Schema Object, não um Reference Object) — a própria spec mostra
isso no exemplo `Cat`/`Pet`. O prompt foi corrigido para explicar o *porquê* em vez de só
proibir. Chegou-se a considerar um sanitizador determinístico no Patcher, mas a decisão
foi não corrigir o LLM silenciosamente: o lugar dessa garantia é o Validator estrutural,
quando ele existir.

**Modelo.** O gerador rodava com `gpt-4.1-mini`. A troca para `gpt-5.6-luna` (o mesmo que
gerou os bancos novos) esbarrou numa incompatibilidade de API que vale anotar: o endpoint
`/v1/chat/completions` recusa *function calling* enquanto o raciocínio está ativo, e todos
os nós usam `with_structured_output(method="function_calling")` — necessário porque
`paths` e `components.schemas` têm chaves abertas, que o modo estrito rejeita. Toda
chamada do Patcher falhou com HTTP 400 e o YAML saiu vazio (0/32 folhas), o que à primeira
vista parecia uma regressão de qualidade e não era. A solução provisória foi
`reasoning_effort=none`, adicionado como `REASONING_EFFORT` em `settings.py`; a solução
melhor seria usar o endpoint `/v1/responses`, onde raciocínio e function calling
coexistem, e isso ficou pendente. Com o luna, os `$ref` saíram 9/9 limpos e a chamada do
Patcher caiu para ~2,8 s.

**Tempo de execução.** O pipeline completo para o PerfMnS (1 operação, 17 regras, sem
legado) leva cerca de **21,5 s** de ponta a ponta, incluindo Planner (que faz uma chamada
por grupo de schema) e Patcher. É um caso pequeno; serve como piso de referência, não como
estimativa para o ProvMnS, que tem 33 operações planejadas.

---

## A métrica de comparação, e por que um número só não servia

Durante as execuções o placar usado era "quantas folhas do YAML batem com o alvo", e ele
enganava. Uma execução cujo contrato estava essencialmente exato marcava 18/32, porque a
métrica pesa uma frase escrita à mão igual a um tipo: as descrições geradas diziam a coisa
certa com outras palavras e contavam como erro. O número não distinguia um tipo errado de
um resumo reescrito.

A comparação foi então versionada em `src/openapi_generator/eval/`, separando as folhas em
três grupos. **Contract** — paths, métodos, schemas, tipos, `$ref`, `enum`, `required` — é
o que a API promete, e uma falha ali é defeito real. **Metadata** — `info`, `servers`,
`externalDocs`, `openapi` — descreve o documento e costuma faltar porque nenhuma regra
cobre esses blocos. **Prose** — descrições e resumos — só precisa *significar* o mesmo, e
sua contagem de acerto exato lê-se como pista de similaridade, não como correção. Folhas
de contrato a mais são listadas mas não penalizadas, já que uma regra pode afirmar o que o
alvo deixa implícito (a lista `required` derivada da tabela de atributos mandatórios é o
caso concreto). Há também a checagem de `$ref` com chave irmã, e o CLI
(`python -m openapi_generator.eval gerado.yaml referencia.yaml`) sai com código diferente
de zero quando o contrato está incompleto, de modo a servir de gate.

Aplicada às execuções, a separação revelou o que o número escondia — ver a tabela na seção
seguinte. Fica a lição metodológica para a dissertação: uma métrica agregada sobre um
artefato heterogêneo mede principalmente a sua parte mais volumosa, que aqui era a prosa.

---

## O teste entre modelos, com o mesmo banco e o mesmo pipeline

Com o banco limpo (21:45) e o pipeline já corrigido, as duas execuções variando apenas o
modelo deram:

| | contrato | prosa | tempo |
|---|---|---|---|
| `gpt-4.1-mini` | 10/19 (53%) | 0/8 | 29,1 s |
| `gpt-5.6-luna` | **18/19 (95%)** | 0/8 | **21,6 s** |

Os dois produzem os quatro schemas, os `$ref` todos limpos (9/9), paths e métodos exatos,
e ambos zeram em prosa. A diferença inteira está localizada num único ponto: as nove
folhas que o mini perde estão todas dentro do `allOf[1]` de `NotifyThresholdCrossing`. Ele
emitiu `allOf` com um único membro e pendurou `type` e `properties` ao lado, fora da
composição — o que é OpenAPI válido e um parser resolve de forma parecida, mas achata a
distinção entre "herdo de X e acrescento Y" e "herdo de X, e por acaso também tenho Y
aqui". Cada propriedade, tipo e `$ref` está individualmente correto; o que está errado é a
estrutura. E isso apesar de o prompt mostrar a composição de dois membros literalmente.

O luna ser ~25% mais rápido é contraintuitivo para um modelo maior, e a hipótese é menos
retrabalho de formatação.

Vale registrar o padrão que se repete: onde há verificação determinística — o gate de
regras, a Phase C corrigida, o roteamento por tipo — os dois modelos convergem; onde o
resultado depende de o modelo interpretar bem uma instrução de forma, o modelo importa.
Isso sugere que o caminho para robustez não é escolher um modelo melhor, e sim mover o que
for possível da instrução para a verificação.

Um percalço que vale anotar por ser um falso alarme instrutivo: a primeira tentativa com o
luna produziu um YAML vazio (0/32 na métrica antiga), o que pareceu regressão brutal de
qualidade. Era HTTP 400: o endpoint `/v1/chat/completions` recusa *function calling*
enquanto o raciocínio está ativo, e todos os nós usam `with_structured_output(method=
"function_calling")` — necessário porque `paths` e `components.schemas` têm chaves abertas,
que o modo estrito rejeita. Resolvido com `reasoning_effort=none` (via `REASONING_EFFORT`
em settings); a saída melhor seria o endpoint `/v1/responses`, onde os dois coexistem, e
isso ficou pendente. Lição: uma queda drástica de qualidade merece checar o transporte
antes de culpar o prompt ou o banco.

---

## Dois campos a mais que o alvo não tem — e que são melhoria

Documento analisado: `data/outputs/test_pipeline/PerfMnS_taxonomy_check.yaml`, gerado com
`gpt-5.6-luna` a partir do banco `rules_bank_28532-i00_full_20260818_214526.json`
(17 regras, todas validadas), sem YAML legado — só regras e spec. Comparado contra
`data/inputs/legacy/PerfMnS/rel_18_TS28532_PerfMnS.yaml`.

Ele traz dois elementos ausentes do YAML oficial da 3GPP: um `operationId` na operação e
uma lista `required` dentro do `NotifyThresholdCrossing`. A métrica os classifica como
"folhas de contrato que a referência não tem", e a pergunta natural é se são invenção
indevida. Conferindo contra a especificação OpenAPI 3.0.1, os dois estão montados
corretamente — e um deles representa mais fidelidade ao documento normativo 3GPP do que o
próprio alvo.

**Onde isso está na referência.** A especificação usada é o `openapi_spec_3.0.1.md`,
obtido do repositório oficial da OpenAPI Initiative e mantido em
`openapi_rulesbank/data/references/openapi_reference/`. O `required` aparece na seção
*Schema Object → Properties*, entre as propriedades tomadas diretamente do JSON Schema e
usadas sem alteração, com a forma canônica exemplificada em *Schema Object → Examples →
Simple Model*: um array de strings, irmão de `type` e `properties`, cujos nomes existem
entre as propriedades declaradas. O `operationId` está em *Operation Object → Fixed
Fields*: "Unique string used to identify the operation. The id MUST be unique among all
operations described in the API. Tools and libraries MAY use the operationId to uniquely
identify an operation, therefore, it is RECOMMENDED to follow common programming naming
conventions."

**Por que o modelo sabe disso: o RAG da referência.** Esse documento foi fatiado e
indexado numa coleção Qdrant própria (`openapi_reference`, embeddings
`all-MiniLM-L6-v2`), separada da coleção da spec 3GPP. A cada operação o Patcher faz duas
buscas: uma na spec 3GPP, com vocabulário de domínio ("POST /notificationSink —
notifyThresholdCrossing"), e outra nessa coleção. A segunda busca **não** usa vocabulário
3GPP — essas palavras não existem no documento OpenAPI — mas sim os nomes das construções
que as regras da operação vão produzir, derivados delas por `_openapi_reference_query`.
Para o PerfMnS a consulta sai como *"Responses Object status codes content — Reference
Object $ref — Schema Object composition allOf oneOf anyOf"*, e os dez trechos recuperados
são exatamente as seções que governam o que está para ser escrito. É assim que o texto
normativo do Schema Object e do Operation Object chega ao prompt.

Isso responde à pergunta de *como* o gerador melhora o documento: não é palpite do modelo
sobre OpenAPI, é o modelo lendo a especificação no momento de escrever. O contraste com a
versão anterior é ilustrativo — quando as duas buscas compartilhavam a mesma consulta em
vocabulário 3GPP, a coleção da referência devolvia trechos sobre Callback Object e Example
Object, e o Patcher escrevia `$ref` sem nunca ter recebido a definição do Reference Object.
Corrigida a consulta, a cláusula de exclusividade do `$ref` passou a chegar, e os `$ref`
saíram 9/9 limpos.

**`required` — melhoria com procedência completa.** O gerado segue a forma canônica à
risca: seis strings, todas presentes em `properties`, colocadas dentro do membro
`type: object` do `allOf`. Esse último ponto é onde mais se poderia errar — como `required`
é do JSON Schema, ele se aplica ao objeto em que está, e colocá-lo no nível externo da
composição afirmaria algo sobre os campos herdados do `NotificationHeader`, o que seria
outra coisa.

O mais relevante, porém, é a origem. A tabela 12.3.1.2.4.2.1-1 da TS 28.532 tem uma coluna
**"S"** (Support) marcando cada atributo como `M` (mandatory) ou `O` (optional): seis
atributos próprios são `M`, e apenas `additionalText` é `O`. O rulesbank leu essa coluna e
produziu a regra [12] — *"The mandatory own attributes of NotifyThresholdCrossing are…"* —
listando exatamente os seis; o gerador a traduziu no construto OpenAPI que expressa essa
semântica. A cadeia spec → regra → YAML está completa e auditável.

E o YAML oficial da 3GPP **não expressa a coluna S**: perde, ao traduzir para OpenAPI, uma
informação que o próprio documento normativo carrega. Ou seja, o gerado é aqui mais fiel à
TS 28.532 do que o artefato que usamos como alvo. Isso é interessante para a dissertação
porque inverte a leitura usual de que divergir do alvo é errar: o pipeline, ao cruzar a
tabela normativa com a especificação OpenAPI, recuperou uma informação que a tradução
manual havia descartado.

**`operationId` — conforme, e útil, mas sem lastro em regra.** O campo não é obrigatório,
mas quando presente tem duas exigências, ambas satisfeitas: unicidade no documento, e
nomenclatura no estilo dos exemplos da própria spec (`getPetsById`, `updatePetWithForm`).
O gerado emite `notifyThresholdCrossing`, em lowerCamelCase, derivado do `source_name` das
regras — que é o nome IS da notificação na 3GPP. A diferença em relação ao `required` é a
origem: nenhuma regra menciona `operationId`, ele existe porque o prompt do Patcher manda
produzi-lo. É o mesmo padrão do `requestBody` inferido, e tem valor prático — um gerador de
cliente produz `notifyThresholdCrossing()` em vez de `post_notificationSink()`.

Consequência para a métrica: chamar esses campos de "extra" e listá-los junto de
divergências reais mistura duas coisas. Uma categoria própria — melhorias conformes sobre
o alvo — separaria "o gerador acrescentou algo correto" de "o gerador errou", que hoje
aparecem sob a mesma contagem.

---

## A taxonomia de tipos de regra, e o que estava se perdendo por causa dela

O banco emite **oito** tipos de regra, mas o pipeline só conhecia seis. Os dois ausentes
não falhavam ruidosamente: `_rule_group_key` devolvia `None` para eles, e uma regra que o
roteador não reconhece é descartada do plano antes de qualquer LLM vê-la. Ou seja, não era
uma decisão ruim sobre a regra — era a regra nunca chegar a ser julgada.

O `callback` era o caso com dano concreto. As regras de callback aparecem em duas formas:
algumas com `rule_type: callback`, outras descrevendo a operação *do próprio callback* com
tipos comuns (`path_operation`, `request_body`, `response`) e a cadeia inteira dentro de
`openapi_object`. Nenhuma das duas era parseada, e no banco do ProvMnS isso transformava
doze regras em paths de topo como
`/{className}={id}.callbacks.notifyMOICreation.{request.body#/…}.post` — strings pontuadas
tratadas como URL, que servidor nenhum roteia. Um Callback Object pertence *dentro* da
operação que o registra, então ambas as formas passam a ancorar ali, com o cuidado de que
o método que aparece **depois** de `.callbacks.` é o do callback e não o do registrador.

Descobriu-se também que a taxonomia estava duplicada em quatro lugares e já divergente:
`TargetOperation.source_rule_ids` listava sete tipos omitindo `callback`, e o docstring do
Planner ainda dizia três destinos quando já eram quatro. Passou a viver uma vez só em
`schemas/rule_types.py`, com cada tipo registrando onde aterrissa e como deve ser escrito.

Essa definição alimenta também o prompt do Patcher, que antes descrevia colocação para
apenas três construtos, espalhados em seções separadas, e não dizia **nada** sobre
callbacks — o modelo tinha de inventar uma forma para um construto que ninguém lhe
explicou. A taxonomia completa é injetada, e não só os tipos presentes no banco da vez,
para que a orientação valha para qualquer MnS.

Fica a observação de fundo, que vale para a dissertação: um roteador que devolve `None`
silenciosamente é a mesma classe de defeito que a Phase C descartando schemas alcançáveis.
Nos dois casos o pipeline seguiu até o fim relatando sucesso enquanto perdia conteúdo, e
em nenhum deles o gate de qualidade das regras chegou a opinar. Perda silenciosa parece
ser o modo de falha característico deste tipo de arquitetura, mais do que decisão errada.

---

## Os blocos de nível de documento: `info`, `externalDocs` e `servers`

Faltavam no gerado, e a investigação de onde o YAML oficial os tira acabou sendo mais
instrutiva que a correção. O primeiro impulso foi óbvio e errado: o Anexo A da TS 28.532
traz o YAML de referência **literal**, um por MnS (A.1.1 ProvMnS, A.4.2 PerfMnS, e assim
por diante), com `servers`, `externalDocs` e `info.description` prontos. Usá-lo como fonte
foi descartado de imediato, e a razão vale registrar: o anexo é o resultado publicado
*depois* de alguém escrever o documento, não a fonte normativa de onde ele saiu. Um
pipeline que lesse dali estaria copiando o próprio alvo e o experimento perderia o sentido.
Toda a extração descrita a seguir ignora os anexos deliberadamente.

Ignorado o anexo, os três blocos se separaram em duas naturezas bem diferentes.

**`info` e `externalDocs` — estão na capa.** O número da especificação, a versão, a
release, o assunto e o aviso de copyright aparecem na primeira página, escritos por
extenso. Nenhuma regra cobre isso, e nem deveria: descrevem o documento, não a API. A
primeira implementação foi determinística, casando padrões de tabela Markdown — e
funcionou, extraindo `externalDocs` e o copyright idênticos ao alvo. Foi descartada mesmo
assim, e o motivo é o que interessa: a entrada pode ser PDF ou DOCX, e ainda que a
conversão seja sempre `pandoc -s`, cada formato produz uma capa diferente. DOCX preserva a
estrutura de tabela; PDF vira texto corrido com quebras arbitrárias. Os valores estão
sempre escritos, mas a posição não é previsível — e um parser afinado para um formato
falharia no outro **em silêncio**, devolvendo metadata parcial sem avisar. Uma leitura por
LLM sobre os primeiros 8 KB resolve isso: o modelo lê a página como uma pessoa leria,
ignorando os artefatos de conversão. Custo de uma chamada por documento.

Uma distinção de idioma que ficou explícita nos prompts: `subject` e `copyright` são
**citações** e saem no idioma da página, porque traduzi-las falsificaria o documento; já a
explicação que a LLM escreve sobre o `servers` é texto **novo** e sai sempre em inglês, já
que uma OpenAPI é lida por um público internacional.

**`servers` — é interpretação, não extração.** Aqui a tentativa determinística falhou de
um jeito instrutivo. As cláusulas normativas de URI trazem o template
(`Resource URI: {MnSRoot}/FaultSupervisionMnS/{MnSVersion}/alarms`) e uma tabela definindo
cada variável, e a fronteira entre servidor e path é exatamente a distinção que a tabela
já faz: variável definida **por referência a outra cláusula ou documento** é
infraestrutura — diz onde o serviço mora — enquanto variável explicada em prosa identifica
um recurso. Cortar após a última de infraestrutura funcionou para ProvMnS e FaultMnS.

Falhou no PerfMnS, e não por detalhe de implementação. Aquele serviço não declara URI de
recurso nenhuma: a cláusula 12.3.1.2.3.2.1.2 diz apenas que "the resource URI is provided
by the notification subscription", porque o servidor fica do lado do **consumidor**. Sem
template para casar, o heurístico caiu num fallback e devolveu o prefixo do FaultMnS —
plausível, silencioso e errado. Esse é o modo de falha característico de um parser diante
de um documento que descreve a mesma coisa de outra forma: não erra dizendo "não sei",
erra dizendo algo convincente.

Passou então a ser uma chamada de LLM sobre as cláusulas de URI localizadas por regex
(que continua útil como **coletor**, não como decisor) mais os trechos do RAG 3GPP. O
resultado nos três serviços: ProvMnS e FaultMnS com o prefixo correto e as variáveis de
infraestrutura, e PerfMnS respondendo que a especificação não declara servidor — citando a
prosa sobre o consumidor e descartando explicitamente as URIs dos outros serviços. A
distinção entre "o parser não encontrou" e "a especificação genuinamente não declara"
passou a existir, e é justamente a que um regex não consegue fazer.

Quando não há servidor, entra um Server Object em branco. A primeira versão inventava um
`url: '{server}'` com variável própria e extensões `x-openapi-gen-placeholder: true` — o
que veste uma lacuna de decisão e obriga o leitor a descobrir que a URL não significa nada.
A forma final é `url` vazio, `description` com a mensagem fixa do gerador ("The server
could not be determined from the specification.") e `x-openapi-gen-note` com o raciocínio
da LLM, separado de propósito: o prefixo `x-` sinaliza conteúdo gerado, e o leitor sabe
qual metade é mensagem fixa da ferramenta e qual é a explicação de um modelo.

**Apresentação.** Ao lado disso, três ajustes para o gerado ler como o original. A
proveniência, que estava espalhada em sete chaves `x-` soltas no meio do `info`, virou um
único bloco `x-openapi-generator` no topo, separado por uma linha de comentário do que a
especificação diz que o documento é — a máquina se anuncia primeiro e depois sai da frente.
Os blocos de topo passaram a ser reservados na ordem canônica pelo Loader (os vazios são
descartados na escrita), porque o YAML é gravado sem ordenação e a ordem de inserção é o
que o leitor vê. E os itens de lista passaram a ser indentados sob a chave que os contém,
via dumper próprio: o `safe_dump` os escreve alinhados com o pai, o que dá a impressão de
que `- url:` está no mesmo nível de `servers:`.

Resultado da rodada: contrato em 18/19 (95%), `$ref` 9/9 limpos, `externalDocs` idêntico ao
alvo, ~26 s de ponta a ponta. As duas divergências de metadata que sobram têm a mesma
causa e não são resolvíveis sem escopo por MnS: `title` traz o nome do documento inteiro
("TS 28.532 Generic management services") em vez do serviço, porque a capa nomeia o
documento e a TS 28.532 define seis serviços; e `version` traz 18.0.0 da capa contra o
18.1.0 do alvo, que é versionamento editorial do YAML publicado, não do documento
normativo.

---

## O desenho do Reflector e do Validator no gerador

Os dois nós eram stubs desde o início, e enquanto foram, nada verificava o que o Patcher
produzia: o laço de retry por operação nunca disparava, e um erro de forma como o `$ref`
com chave irmã chegava intacto ao YAML final. Ao especificá-los, ficou claro que a ideia
aqui é diferente da do `openapi_rulesbank`, e a diferença justifica um desenho mais
simples. Lá se julga se uma **regra** pertence ao documento, o que é sempre leitura de
texto normativo; aqui se verifica um **YAML já construído**, e boa parte disso é
verificável sem LLM nenhuma — um `$ref` ou resolve ou não resolve.

O fluxo acordado, em ordem:

1. **Determinístico** — acha os problemas de forma, sem LLM. São candidatos, não
   veredictos: a checagem vê forma, não intenção.

2. **Reflector, primeira chamada** — vai por partes: cada regra, cada operação, cada
   pedaço do documento. Recebe os achados do determinístico como contexto, para não
   gastar a chamada reencontrando o que já se sabe, e procura o que só a leitura pega —
   se o fragmento diz o que a regra queria dizer.

3. **Reflector, segunda chamada** — agora sobre o **documento inteiro** montado pelo
   Patcher, recebendo o resultado do determinístico e o da primeira chamada. Aqui ele
   **revalida todos os problemas** levantados até então, descarta os que não se sustentam,
   e procura o que não se vê olhando um pedaço só. É também nesta chamada que se decide o
   que precisa ser **adicionado** e o que precisa ser **removido** — uma ausência ou um
   excesso raramente é visível dentro de uma parte isolada. Sai daqui a lista conferida.

4. **Validator** — recebe os problemas já conferidos, olha o YAML, e escreve os
   **consertos** para o Patcher. Suas instruções são de três tipos: **alterar**, **remover**
   ou **adicionar**. Ele não repete o que já foi dito nas etapas anteriores; transforma
   observação em ação.

5. **Patcher** — pega o documento gerado anteriormente e vai mudando cada coisa, ou
   adicionando, ou removendo, conforme o Validator disse.

A divisão de responsabilidade é o ponto: **o Reflector analisa e confere, o Validator
instrui**. Quem descreve o problema é quem o notou; quem escreve o conserto é quem
responde pelo retry. Manter as duas coisas separadas evita que a análise já venha
enviesada por uma solução, e é o mesmo princípio de projeto que o rulesbank adotou ao
deixar o refletor *analisar* e o validador *decidir*.

Vale registrar uma implicação sobre o Patcher que decorre disso: ele **regenerava** o
fragmento do zero a cada tentativa, e o fluxo acima pede outra coisa — que parta do
documento anterior e aplique as mudanças pontualmente. É uma diferença de natureza, não
de grau: regenerar arrisca perder o que já estava certo, enquanto editar preserva e
reduz a variação entre tentativas. O `patcher_node` ganhou por isso dois modos — construir
uma operação, ou corrigir o documento montado — e o grafo os separa por uma aresta
condicional, porque um documento corrigido em lugar não tem fragmento para o Assembler
mesclar e volta direto para a revisão.

A topologia mudou junto. O Assembler passou a rodar **logo após o Patcher**, não depois da
revisão: ele mescla e **grava o documento a cada operação**, como o Builder do rulesbank
salva sua bank a cada seção, de modo que uma falha adiante custe as operações que faltam e
não as que já ficaram prontas. Só quando todas estão no documento é que a revisão começa,
e ela lê o que foi efetivamente escrito — não o fragmento que o Patcher devolveu, que o
merge pode ter alterado.

---

## Quando o revisor é o que estraga: três bugs e oito prompts

Esta parte é a mais instrutiva da sessão, e o registro honesto importa mais que o
resultado. Implementados o Reflector e o Validator, o pipeline passou a **piorar** o
documento. O contrato, que saía em 18/19 sem revisão, caiu para 13/19 na primeira
execução com ela, e cada rodada de correção introduzia algo novo: envolveu propriedades em
`allOf` só para lhes anexar `description`, moveu o `required` para fora do membro que o
governa, acrescentou uma propriedade `notificationType` que o `allOf` já herda, removeu o
`additionalText` que tem regra própria, e chegou a mandar apagar o schema
`PerfMetricDirection` inteiro alegando que "nenhuma regra o sustenta" — quando duas regras
o definem.

A resposta natural foi ajustar o prompt, e foi o que fiz **oito vezes**: estreitar o que
conta como problema, exigir evidência, proibir mover construtos válidos, explicar que
`allOf` herda, que ausência não refuta, que referências externas são inverificáveis por
construção. Cada ajuste fechava um tipo de falso positivo e outro aparecia na execução
seguinte. A leitura que eu vinha fazendo — "o modelo trata produzir uma lista como a
tarefa, e uma lista vazia como falha" — era plausível e estava, em boa parte, errada.

Havia **três bugs determinísticos no código que eu mesmo escrevera**, e eles explicam a
maior parte da oscilação:

**1. A correção apagava schemas.** O `_apply_corrections` fazia
`updated["components"] = corrected.components`. O prompt pede o documento inteiro de
volta, mas isso é uma esperança, não uma garantia: quando o modelo devolvia apenas o
schema que o conserto tocou, a atribuição **substituía o bloco** e os outros três
desapareciam. Só a execução com três rodadas de correção foi afetada, o que fez o problema
parecer intermitente e ligado ao prompt. A correção é mesclar chave a chave e só remover o
que um conserto `remove` nomeia explicitamente.

**2. O relatório do modelo vazava para o documento.** O `PatcherCorrectedDocument` tem
`applied` e `skipped` — a prestação de contas do modelo sobre o próprio trabalho. A saída
estruturada convida a aninhá-los dentro dos blocos devolvidos, e o merge os tratava como
conteúdo: um `applied:` com caminhos pontilhados apareceu **dentro de
`components`** no YAML publicado. São descartados agora, onde quer que surjam.

**3. O recorte por operação contava os schemas errado.** Este é o mais interessante. Na
etapa 1 o Reflector revisa uma operação por vez, com o pedaço do documento que ela alcança;
depois, os schemas que nenhuma operação alcançou passam por uma revisão à parte. O conjunto
`covered` era montado com uma regex sobre o YAML do recorte — `^  (\w+):` — que casa a
chave `schemas`, indentada com dois espaços, e **não** os nomes dos schemas, indentados com
quatro. Resultado: `covered = {"schemas"}`, e os três schemas alcançáveis eram considerados
órfãos e **revisados uma segunda vez, fora do contexto da operação e sem as regras em
foco**. Era exatamente ali que nasciam as queixas contra `PerfMetricDirection` e
`PerfNotificationTypes`: vistos soltos, sem a operação que os usa, pareciam injustificados.
A travessia de `$ref` que o recorte já fazia passou a ser uma função própria, usada tanto
para montar o recorte quanto para saber o que ele cobriu.

Com os três corrigidos, o ciclo passou a convergir na primeira passagem: o Reflector
levanta um candidato, a segunda chamada o descarta, o Validator não emite conserto, e o
documento sai em 18/19 — o mesmo que o Patcher produzia sozinho, agora verificado.

Três lições que valem para a dissertação. A primeira é que **num pipeline de agentes, um
defeito de código se disfarça de defeito de prompt**: o comportamento é probabilístico por
natureza, então a oscilação parece vir do modelo, e a tentação de reescrever a instrução é
imediata. Oito rodadas de ajuste de texto foram gastas antes de eu olhar o código, e só
olhei porque foi pedido que verificasse os recortes antes de rodar de novo. A segunda é
que **verificar a estrutura intermediária é mais barato que interpretar a saída final**:
os três bugs apareceram em minutos assim que se checou o que cada etapa recebia — se cada
recorte fechava referencialmente, se a soma dos recortes cobria o documento, se nada era
revisado duas vezes. A terceira é sobre o dano assimétrico de uma etapa de revisão: um
gerador que erra produz um documento ruim, mas um revisor que erra **estraga um documento
bom**, e é isso que faz dele um componente perigoso de acrescentar. Vale exigir dele
evidência citável antes de agir — foi o que finalmente estabilizou o comportamento.

Uma última observação metodológica: quase todo defeito que o revisor tentou consertar era
falso positivo, e em nenhuma execução desta sessão ele encontrou um problema real no
documento. Isso não prova que a revisão seja inútil — prova que ela não tem o que fazer
num caso limpo, com uma operação e um banco de regras sem defeitos. O teste que faltava, e
que fica pendente, é o ProvMnS: 34 operações, 240 regras, defeitos concretos. É lá que se
saberá se a revisão agrega ou apenas encarece.

---

## O ProvMnS em escala: a Phase C, e o que ela estava escondendo

O teste que faltava — anotado na seção anterior como "o que se saberá no ProvMnS" —
finalmente rodou. E o resultado é interessante menos pelo ganho do que pelo que o ganho
descobriu.

O ponto de partida foi o run de 15/set contra o banco i20 (122 regras): **32/191 no
contrato, 17%**. Contra os 95% do PerfMnS, a diferença não era de qualidade do banco —
verificou-se que ele define exatamente os 16 schemas que o alvo tem, e que o roteamento
entrega todas as 122 regras. A perda estava na Phase C do Planner, que pergunta a um LLM
quais operações usam cada schema e tratava "nenhuma operação usa" como motivo para
**descartar a regra**. Com 4 operações e 16 schemas, a cadeia operação→schema→schema
ficou longa demais e 49 regras caíram.

A correção é de uma linha de lógica e cabe numa frase: uma regra que define um schema já
é a autoridade de que o schema pertence ao documento. Qual operação o referencia é outra
pergunta, e responder "nenhuma" não autoriza jogar a definição fora — `components.schemas`
é irmão de `paths`, não apêndice dele, e uma spec pode declarar um tipo que nenhuma
operação usa. Um schema não reclamado passa a ser anexado à primeira operação, que apenas
o carrega.

  |                          | 15/set  | 19/set  |
  |--------------------------|---------|---------|
  | contrato                 | 32/191 (17%) | **87/191 (46%)** |
  | regras cobertas          | 97/122 (49 gaps) | **121/122 (1 gap)** |
  | schemas do banco no doc  | 5/16    | **16/16** |
  | `$ref` quebrados         | 16      | **0**   |
  | `components` dentro de `paths` | sim | **não** |
  | custo / tempo            | US$ 0,166 / 537s | US$ 0,143 / 358s |

A hipótese de que um defeito dominante gera vários sintomas distintos se confirmou em
parte, e a parte que **não** se confirmou é a mais instrutiva. Dos três problemas que eu
esperava ver cair junto:

- o `$ref` quebrado zerou, mas por duas vias — só uma era conserto. Os tipos importados
  saíram do contador porque ganharam definição local, não porque foram resolvidos;
- as propriedades sem tipo: a instância registrada sumiu e **o total subiu de 3 para 5**;
- o `components` aninhado não ocorreu, mas **nada foi corrigido** — a guarda no Assembler
  segue não implementada, e uma execução limpa não é prova de eliminação.

A lição é sobre o instrumento, não sobre o pipeline: **os três foram avaliados por
contadores que não distinguem a causa**. Um contador de `$ref` quebrados dá verde tanto
para "a referência foi resolvida" quanto para "o alvo foi inventado"; um contador de
instâncias fecha um caso enquanto o total piora; e a ausência de um erro de modelo numa
execução não é sinal nenhum. Declarei os três resolvidos antes de olhar para isso, e tive
de reabrir dois. É a mesma armadilha que o `RASTREIO` do rulesbank registra três vezes —
suspeitar do instrumento antes de comemorar o fenômeno — e ela não se evita sabendo dela,
só conferindo.

O ganho de P1 é real e não depende de nenhum desses contadores: 17% → 46% no contrato é
medido contra o YAML alvo, item a item.

### O que o conserto revelou, e que é pior de ver

Os `$ref` quebrados zeraram, mas **não só pela via prevista**. Os schemas que o banco
define voltaram — isso era o esperado. Os doze tipos *importados* (`ErrorResponse`,
`Uri`, `DateTime`, `Filter`, `SpecificProblem`…), que o banco referencia e deliberadamente
não define porque pertencem a outro documento, resolveram de outra maneira: o Patcher os
**materializou localmente**, inventando uma definição para cada um. O documento passou a
ter 28 schemas onde o alvo tem 16.

Isso é tecnicamente válido — nenhuma referência aponta para o vazio — e semanticamente
errado: um tipo importado com definição local é uma cópia que vai divergir da original na
próxima release. E a troca é ruim do ponto de vista de diagnóstico: antes o defeito
gritava (`$ref` apontando para nada), agora ele é silencioso (um schema a mais, plausível,
no meio de 28). Registrado como P7 no `PROBLEMS.md`, com a ressalva de que o conserto
completo depende de o rulesbank preencher o campo `file` das referências — medi e ele vem
vazio em 48 das 57.

### O laço de correção passa a ser o defeito dominante

Com a Phase C corrigida, o que sobrou mudou de natureza. Duas regressões, e as duas do
laço Reflector→Validator→Patcher:

O **PUT desapareceu do documento**, e com ele os cinco callbacks — o alvo tem
`notifyMOICreation`, `notifyMOIDeletion`, `notifyMOIAttributeValueChanges`, `notifyEvent`
e `notifyMOIChanges`; o gerado tem zero. O log é inequívoco quanto a ele ter existido: às
16:17:02 o Assembler registra `merged put /{className}={id}`. O que veio depois foram três
rodadas de correção, e na rodada 2 o Validator pediu exatamente **um `remove`**.

E o laço **não convergiu**: terminou com `3 fix(es) outstanding (reached 3 correction
rounds)`, tendo aplicado 0 de 2 fixes na rodada 3. Está girando sobre algo que não
consegue resolver.

Isso obriga a reposicionar o diagnóstico anterior sobre a revisão. Eu havia registrado que
ela "não viu nada" — falso. Ela viu bastante e pediu 17 consertos. O problema é outro, e
é mais grave: **um `remove` equivocado custa uma operação inteira com seus callbacks**, e
esse custo supera tudo o que ela consertou de bom. Confirma-se a assimetria já anotada na
sessão anterior, agora com número: um gerador que erra produz documento ruim; um revisor
que erra estraga documento bom. A diferença é que agora o revisor tem poder de remoção.

---

## O que cada nó vê, e o preço de esconder

A sessão de 20/set começou com uma pergunta do usuário sobre o cabeçalho do documento e
terminou reescrevendo como três nós conversam. O fio condutor apareceu tarde, e vale
registrá-lo porque explica defeitos que eu vinha tratando como independentes: **em cada
caso, a informação existia e era retida antes de chegar a quem precisava dela.**

  O Patcher recebia uma lista de nomes de schemas — `"Resource, Scope, …"` — e nada da
  estrutura do documento em que escrevia. Cada chamada, sem saber o que a anterior havia
  feito, reescrevia os parâmetros do path dentro da própria operação: três cópias dos
  mesmos sete parâmetros, deslizando os índices e estragando a comparação.

  O Reflector recebia o documento truncado em 24 000 caracteres (o documento tinha
  26 756) e fatias montadas pelo plano. Os últimos 10% nunca eram revisados, em silêncio;
  e nada que não fosse uma operação planejada — um `components` aninhado, um método solto
  sob `paths` — chegava à revisão. Eu vinha escrevendo que "o revisor não viu o
  problema"; a verdade é que ele não podia ver.

  A chamada de correção recebia o documento inteiro e tinha de **devolvê-lo inteiro**,
  reescrito, para aplicar sete consertos pontuais.

Medir o custo foi o que decidiu a questão. O prompt do Patcher já usava ~10k tokens por
chamada; o documento inteiro custa ~6,7k e a estrutura relevante, 884. Estávamos
economizando menos de mil tokens de contexto e pagando com um defeito estrutural.

### A reescrita que ninguém queria

O caso da correção é o mais instrutivo, porque a intenção estava escrita no código e a
implementação fazia o contrário. O comentário dizia "o documento é editado, não gerado de
novo"; o schema pedia *"the complete `paths` block"* e *"the complete `components`
block"*.

Boa parte das defesas espalhadas pelo Patcher existia para conter essa reescrita: mesclar
chave a chave em vez de trocar blocos, tirar `applied`/`skipped` de dentro do YAML,
recusar remoções que ninguém pediu. Cada uma foi um bug caro na sua vez. Nenhuma atacava
a causa.

E o desenho quebrou justamente quando o pipeline melhorou. Com os schemas de volta (P1) o
documento cresceu, os ~4 900 tokens de saída passaram dos 60 segundos de timeout, três
tentativas falharam, `with_structured_output` devolveu `None` em vez de lançar, e
`corrected.paths` derrubou a execução inteira. Um defeito que piora à medida que o
trabalho dá certo é o pior tipo de defeito para se ter.

O conserto separou três coisas que estavam confundidas — **recebe** o documento inteiro
como contexto, **devolve** só os blocos que mudou, **aplica** alterando só esses blocos —
e o efeito foi imediato: a rodada de correção caiu de 5 minutos (timeout) para 2 e 7
segundos, a execução de US$ 0,17/476 s para US$ 0,096/234 s, e **o laço convergiu sozinho
pela primeira vez**, terminando em `Validator → nothing to fix` em vez de esgotar as
rodadas.

### Um número pior numa execução melhor

O contrato caiu de 46% para 42% na execução mais saudável até hoje — sem `$ref` quebrado,
sem corrupção estrutural, com o laço convergindo. As 28 folhas perdidas estão medidas:

  18 são o posicionamento dos parâmetros. O banco endereça ao path cinco parâmetros cujo
  próprio `rule_text` diz "The HTTP GET operation supports…". O texto está certo e o
  mapeamento errado — defeito do rulesbank, registrado como R1 — e o generator está
  fazendo o correto ao escrever onde a regra endereça. "Consertar" aqui exigiria saber que
  estes cinco pertencem ao GET, que é conhecimento de uma MnS específica.

  10 são o `NotifyEvent`, que saiu como `{type: object}` numa chamada que gerou 17 schemas
  de uma vez (contra 13 antes). Não diagnosticado; registrado como R2.

Vale como advertência metodológica, na mesma linha da nota de 19/set sobre contadores: a
saúde estrutural de um documento e a sua proximidade do alvo são medidas diferentes, e
podem andar em sentidos opostos numa mesma execução. Tratar o contrato como *a* métrica
teria levado a desfazer três consertos bons.

### O anexo que entrava pela porta dos fundos

Um achado lateral, e sério. `_normative_text` cortava os anexos na **última** ocorrência
de "Annex" — mas o sumário lista todos os anexos antes do corpo, e o documento tem A, B e
C. O corte caía no Anexo C, deixando A e B dentro do que o código chamava de texto
normativo. O Anexo A é o YAML alvo.

Ou seja: 610 mil caracteres contendo a própria resposta que o pipeline existe para
derivar estavam disponíveis para a dedução dos `servers` e para qualquer busca sobre o
documento. Não há evidência de que o resultado tenha vindo de lá, mas a garantia que o
código anunciava não existia.

O conserto foi aplicar a redução uma única vez, na leitura do documento, cortando o
sumário primeiro e os anexos no primeiro cabeçalho restante. Ao fazê-lo introduzi uma
regressão imediata e didática: passei a alimentar com o texto reduzido a chamada que lê a
**capa**, que fica acima do sumário. O documento saiu sem versão, sem copyright e sem
`externalDocs`. Duas leituras diferentes precisavam de dois textos diferentes, e tratá-las
como uma só custou uma execução.

---

## Seis divergências com o rulesbank, e o que elas revelam sobre contratos implícitos

O gerador e o rulesbank trocam regras por um contrato que nunca foi escrito em lugar
nenhum: o rulesbank valida o que emite, o gerador documenta o que espera, e ninguém
compara os dois. Levantadas as divergências uma a uma, apareceram seis. Nenhuma estava
quebrando o pipeline — e é justamente isso que as torna interessantes.

**Métodos HTTP.** O gerador reconhecia sete (`get, put, post, delete, patch, head,
options`), o rulesbank valida cinco e rejeita o resto. A lista do gerador vinha do commit
que criou o pipeline, em maio, antes de existir um banco com que concordar, e nunca foi
revista. Nada quebrava, porque os dois extras não podiam chegar — mas dois dos lugares
que guardavam a lista são schemas de **saída estruturada**, onde um `Literal` diz ao
modelo o que ele pode devolver. Oferecido um método que o banco nunca emite, o modelo
acaba devolvendo um, e a operação que ele nomeia não tem regra por trás. A lista passou a
viver uma vez só, em `schemas/rule_types.py`, ao lado da taxonomia — é o mesmo contrato.
Dois regex no Planner soletravam os métodos à mão em vez de ler a constante, então
estreitar só a constante teria deixado os dois ainda aceitando sete; esse é o tipo de
resíduo que faz um conserto parecer aplicado sem estar.

**Keywords de schema e `request_body`.** O guia de tipos listava cinco keywords onde o
banco valida sete: uma regra sobre `items` ou `additionalProperties` passa na validação e
chega ao gerador sem nada dito sobre a forma que pede. E para `request_body` o guia dizia
apenas que o campo "nomeia a parte", enquanto o banco há tempos exige que seja exatamente
`content`. Ao corrigir, caí numa armadilha que vale registrar: escrevi primeiro no campo
`address` do `RuleTypeSpec`, e **só `destination` e `guidance` são interpolados no
prompt**. O conserto ficou invisível para o modelo a que se destinava, e só apareceu
porque a verificação testou o texto gerado em vez do código.

**O nível dos parâmetros.** Esta é a única com consequência real no documento, e a que
mais ensinou. O guia dizia, para `path_parameter` e `query_parameter` com as mesmas
palavras, que ambos vão em `paths.<path>.parameters`, "applies to every method". Os seis
YAMLs oficiais contradizem: em nenhum deles um query param aparece no path item. No
ProvMnS os cinco (`scope`, `filter`, `attributes`, `fields`, `dataNodeSelector`) existem
só dentro do `get`; `put`, `patch` e `delete` não têm nenhum. Oferecidos no nível do path,
seriam anunciados nos quatro.

A primeira tentativa de regra foi "path param no path item, query param na operação" — e
ela estava errada por generalizar o ProvMnS. Conferindo os seis YAMLs: ProvMnS declara
`className` e `id` uma vez no path item, mas FaultMnS, FileDataReportingMnS e
StreamingDataMnS não usam o path item para nada, pondo todos os parâmetros dentro das
operações. O contraexemplo decisivo é o `/connections/{connectionId}/streams` do
StreamingDataMnS: **três métodos**, e `connectionId` repetido em cada um em vez de
declarado uma vez acima. Ou seja, nem "vários métodos → path item" se sustenta. OpenAPI
permite os dois níveis e não prefere nenhum, e os serviços discordam entre si: não há um
"certo" a descobrir no alvo, só duas formas legítimas.

A regra que ficou não tenta escolher por eles. **O endereço da regra decide**: nomeando um
método, o parâmetro pertence àquela operação; nomeando só o path, ao path item, onde uma
declaração serve a todos. Vale igualmente para os dois tipos, não menciona serviço algum,
e acompanha a fonte — o banco escreve o endereço a partir da cláusula de onde extraiu o
parâmetro, então a colocação segue o documento normativo em vez de um estilo de casa. Um
query param sai na operação não por uma regra sobre query params, mas porque o contrato
com o banco faz seu endereço carregar o método.

**As duas que não eram do gerador.** As regras de dentro dos callbacks divergem entre
bancos, não entre repositórios: o de abril usa `path_operation` dentro do callback, o de
setembro usa `callback` com `request_body` e `response` — e é este que o rulesbank valida
hoje. O endereço do callback já está alinhado; o gerador apenas tolera também a forma
antiga, o que é folga, não conflito.

Fica a observação de fundo, que generaliza para além destes dois repositórios: **um
contrato que ninguém verifica deriva em silêncio**. Quatro das seis divergências tinham o
rulesbank certo e o gerador desatualizado, nenhuma causava erro visível, e todas só
apareceram quando os dois lados foram lidos lado a lado. O caso dos `Literal` é o mais
instrutivo — uma divergência inofensiva no código vira um convite ao modelo quando o mesmo
valor aparece num schema de saída estruturada.

E uma lacuna que a varredura revelou sem resolver: o StreamingDataMnS declara parâmetros
`header`, e nenhum dos oito tipos de regra cobre esse caso.

---

## O que ainda falta (para não perder de vista)

O `Reflector` e o `Validator` do gerador são **stubs** — devolvem lista de erros vazia e
o fragmento intocado. Isso significa que o laço de retry por operação nunca dispara e que
nada verifica o que o Patcher produziu. É a lacuna mais séria do pipeline hoje: o erro do
`$ref` com chave irmã, por exemplo, é exatamente o que uma verificação estrutural pegaria.

Faltam também os blocos de nível de documento — `info.description`, `externalDocs`,
`servers` — que nenhum banco de regras cobre (os destinos presentes são apenas `paths.*` e
`components/schemas/*`). Curiosamente, essa informação **existe** na spec: o anexo da
TS 28.532 traz o YAML de referência literal, com `servers`, `externalDocs` e tudo mais.
O Loader já parseia as 696 seções do documento e as guarda em `parsed_spec_sections`, mas
nenhum nó as consome — o Planner inclusive documenta o campo como "unused". Há aí uma
decisão de arquitetura a tomar e que vale explicitar na dissertação: um pipeline
estritamente *bank-driven* e um pipeline *bank + spec fallback* são desenhos diferentes,
com propriedades diferentes de rastreabilidade. Hoje é o primeiro, por omissão mais do que
por escolha deliberada.

Menores, mas anotados para não se perderem: o campo `references` do `openapi_mapping` é
uma lista estruturada de `{file, schema_name}` que o rulesbank preenche justamente para o
consumidor não ter de parsear `openapi_value` atrás de `$ref` — e o gerador o ignora,
lendo a string. A divergência `4XX`/`5XX` contra `default` é fidelidade às regras e não
erro, mas convém decidir se o gerador deve normalizar. E a marcação de proveniência
discutida na seção do `requestBody` — dizer quais elementos vieram de regra e quais foram
inferidos — continua por fazer; ela é o que permitiria medir a inferência em vez de
apenas constatá-la.

**Nada foi medido desde 20/09.** A última geração em disco é das 19:52 daquele dia, e os
três consertos que vieram depois — a revisão enxergando o documento inteiro, o Planner
deixando de descartar schemas, o Patcher editando em vez de reescrever — foram committados
às 20:15 sem nunca terem sido executados. Somam-se a eles os quatro commits de 01/10 sobre
as divergências. Os números que a mensagem do commit `98d0fdc` cita (rodada de correção de
5 min para 2 s, laço convergindo sozinho, US$ 0,17 para US$ 0,096) vieram de execuções
feitas durante aquela sessão, antes do commit; não há execução que meça o estado atual do
código.

Do lado do rulesbank, a mudança do `query_parameter` passou a exigir o método no endereço,
mas **os bancos existentes não o têm**. Enquanto não forem regerados, essas regras caem no
caminho antigo e o comportamento é o de hoje — nada quebra, e nada melhora. Vale decidir se
a regeração do ProvMnS entra na próxima rodada ou se espera.

Falta ainda um tipo de regra para parâmetros `header`, que o StreamingDataMnS usa e que a
taxonomia dos oito tipos não cobre.

## 08/nov/2026 — o que veio do rulesbank e falta conferir aqui

O corte do documento passou a ser o `split_sections` do `qdrant_3gpp_rag` (commit `b2afeb7`), o mesmo do
rulesbank e do corpus: na i20 entram os 7 títulos sem estilo e os de nível 1, e o texto deixa de levar o `#`
solto. Não foi rodado aqui depois disso.

Do lado do banco, seis mudanças mudam o que o gerador recebe; estão em `PROBLEMS.md`, P11–P16, para conferir
com o próximo banco da ProvMnS: `style`/`explode` no valor do `query_parameter`; o mapa do `Resource` como
`additionalProperties`; o `required` só com o que é sempre presente; o corpo de erro sem código em `default`;
a chave do callback pelo campo `notificationRecipientAddress`; e o campo novo `metadata.unresolved_missing`.

## 10/nov/2026 — o código lido contra os documentos, e o banco que mudou por baixo

Desde 20/09 nada foi executado aqui. Em vez de rodar, li o código inteiro contra o
`PROBLEMS.md` e este caderno, e depois contra o que o rulesbank decidiu desde então. As
duas leituras deram resultados de natureza diferente.

**O que o código mostra.** O P10 estava como "não diagnosticado", e a causa está à
vista: o filtro do relatório do modelo só olha o primeiro nível do bloco devolvido, e
nenhum merge confere a forma do que entra. O P4 foi fechado em 20/set pelo prompt, não
por código. Uma execução limpa não provava que o caminho estava fechado; a nota de 19/set
sobre contadores já avisava isso, e a regressão de 19:41 confirmou. Dois achados repetem
consertos registrados como feitos: o Validator ainda trunca o documento em 24 mil
caracteres, como o Reflector truncava, e a proteção contra a resposta estruturada `None`
só existe na chamada onde o erro apareceu. É o padrão que já se viu com a lista de
métodos HTTP em 01/out: o conserto vai para o lugar onde o defeito apareceu, não para
todo lugar que tem a mesma forma. Há ainda dois defeitos que só aparecem fora da ProvMnS
(paths casados por substring, chave de callback com ponto) e uma terceira forma de perda
silenciosa: o `probable_gaps` do Planner é descartado pelo LangGraph, porque a chave não
está declarada no estado. Registrados como P10 (causa) e P17–P25.

**O que o rulesbank mudou.** A errata de 07/nov, a cascata de nome de arquivo de 19/out
e os consertos de 08/nov resolveram do lado do banco parte do que este caderno tratava
como limite do gerador. O R1 está resolvido: os query params vêm endereçados à operação.
O P7 encolheu: o banco de 09/nov traz o `$ref` externo pronto para os tipos comuns, e os
que faltam ele **declara** em `rules_never_completed`. Esse campo é a informação negativa
que o P7 pedia. A fonte do P9 sumiu, e o 4XX/5XX deixou de ser "convenção" e virou um
critério por operação (D.10). Em troca, o banco novo traz defeitos próprios que vão
aparecer no YAML (R3). O mais enganoso é o N.35, parâmetros de path repetidos no DELETE:
vai parecer a volta de um defeito daqui já resolvido, e a origem é a regra.

A lição é a da seção de 01/out, vista de outro ângulo: o contrato entre os dois
repositórios muda dos dois lados, e um diagnóstico daqui envelhece quando o banco muda.
Antes do próximo run, o `PROBLEMS.md` foi alinhado ao `04_Problems_ProvMnS.txt` de lá,
para que cada defeito do YAML seja atribuído ao lado certo.

