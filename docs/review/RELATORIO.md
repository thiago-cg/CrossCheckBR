# Code review do CrossCheckBR — fluxo de agentes, scraping e premissas de design

Data: 2026-09-28 · Base: `main` @ `ed1d168` + diff local (`modelo_fake.py`, `catalogo.json`)
Escopo: experimento de aprendizado. Problemas que só importam em produção (auth, rate-limit, deploy, SSRF hardening) foram **ignorados de propósito**.

Método: 4 sub-agentes em paralelo, cada um com sondas executáveis offline (SerpAPI/LLM/deep crawl falsos) e, no caso de scraping e ReaderLM, HTML real baixado em 28/09. Os achados críticos foram reconferidos no código antes de entrar aqui. Relatórios detalhados, com arquivo:linha e saída de cada sonda:

| Relatório | Tema |
|---|---|
| [r1_agentes.md](r1_agentes.md) | harness do agente, prompts, comunicação entre etapas |
| [r2_scraping.md](r2_scraping.md) | crawler, índice de vereditos, extração de corpo, catálogo |
| [r3_decisao.md](r3_decisao.md) | agregador, corroboração, juiz, BERTimbau, testes |
| [r4_readerlm.md](r4_readerlm.md) | ReaderLM-v2 GGUF: o que é, como servir, onde encaixa |
| [cascata_readerlm.py](cascata_readerlm.py) | protótipo JSON-LD → trafilatura → ReaderLM-v2 |

---

## 1. Resumo: as suspeitas se confirmam

**O sistema hoje não consegue chegar a uma resposta certa, mesmo quando tudo "funciona".** São três falhas independentes, e cada uma sozinha basta para quebrar o resultado:

1. **Não há dado.** O índice de vereditos (o "caminho feliz", peso 0,40) tem 17 documentos: 16 são homepages ou páginas "sobre", todos com corpo vazio, e o único veredito está errado (um `VERDADEIRO` numa matéria do ValorInveste sobre golpes). O crawler extraiu corpo útil em **0 de 36** páginas. O "schema CSS gerado por LLM" **não existe**: os seletores estão escritos à mão como *hints*, e `JsonCssExtractionStrategy` só aparece dentro de strings.
2. **A agregação inverte a direção.** Três checadores que **refutam** a afirmação dão propensão **BAIXA** (score +0,227): o sinal "cobertura ampla" vale −0,5 independente da postura das fontes. As faixas são assimétricas (67,5% do intervalo cai em "baixa"). E a direção do selo é lida por substring de um texto que inclui o nome do portal, então VERDADEIRO do "Fato ou **Fake**" vira ALTA.
3. **O "agente" não é agente, e os fallbacks mascaram falhas.** O agente de descoberta é um plano estático de regex com um "crítico" que olha o host da URL, não o conteúdo. Quando o LLM cai (teto de 50 chamadas/dia ≈ 3 consultas), o fallback `lexico-top3` marca as 3 primeiras fontes catalogadas como `relevante=True` **sem ler nada**, e a etapa continua dizendo "julgamento llm-juiz". No crawler, "No model loaded" é registrado como `[artigo] OK` (18×).

E **nada disso aparece nos testes**: 90 testes verdes, mas nenhum roda o pipeline com descoberta ativa. Na mutação, com todos os `PESOS` em 0,01 ou invertidos, os 43 testes de decisão continuam passando. O único teste que quebra quando se *corrige* o bug principal é o que o trava como comportamento esperado (`test_cobertura_ampla_pesa_mesmo_com_valor_neutro`).

---

## 2. Onde o fluxo quebra

```
entrada ─► [afirmações LLM] ─► [índice BM25] ─► [agente descoberta] ─► [deep crawl] ─► [resumir→juiz] ─► [corroboração] ─► [agregador] ─► nível
               │                    │                  │                    │                 │                  │                 │
          preâmbulo vira       17 docs lixo;      crítico olha host,    teto 200–500KB     perde título,      conta menções,    "ampla" = −0,5
          afirmação;           piso BM25 < score  não conteúdo;         corta G1/BBC;      selo e veículo;    não postura;      sempre; faixas
          "30 mil"→"mil"       mínimo do BM25Plus onda 2 nunca roda     Lupa nunca lida    "relevante":true   data_pub ≠        assimétricas;
                                                                                            literal no prompt  data_publicacao   selo por substring
                                     ▲                                                             ▲
                                     └────── sem LLM: lexico-top3 inventa 3 "relevantes" ──────────┘
```

---

## 3. Achados: falha → por quê → correção escolhida → trade-offs

Severidade medida pelo objetivo do experimento (o sistema dá a resposta certa?), não por risco de produção.

### A. Dados: scraping e índice

#### A1. [Crítico] O índice de vereditos não tem checagens
- **Falha:** `amostra_checagem.json` tem 17 URLs: homepages (`boatos.org/`, `e-farsas.com/`), páginas `/about/`, `/redes-sociais`, hubs. Corpo com 0 caracteres em todas. Testamos 11 afirmações plausíveis (vacina, urnas, ibuprofeno…): nenhuma retornou um veredito relevante. O BM25Plus dá pontuação-base a todo documento (mínimo 1,34 em "urnas fraudadas"), acima do piso `INDICE_SCORE_MIN=1.0`, então **sempre** volta alguma homepage.
- **Por quê:** o índice é populado pelo crawler (A2), que escolhe URLs por heurística de link na homepage e não valida se a página é uma checagem.
- **Correção escolhida:** ingestor por **RSS + JSON-LD `ClaimReview`** das agências, substituindo o crawler como fonte do índice. Só com os 5 feeds testados vieram **210 checagens reais** no mesmo dia. Aos Fatos, Estadão Verifica e Comprova publicam `ClaimReview` com `claimReviewed` e `reviewRating.alternateName` (veredito oficial do editor, sem LLM). Para quem não publica, uma regex de título por agência: 99/100 títulos do G1 Fato ou Fake têm `#FAKE`/`#FATO` e 21/25 do Boatos seguem "É falso que…". A Lupa expõe `NewsArticle.articleBody`.
- **Trade-offs:** RSS cobre só o passado recente (dezenas de itens por feed), então o histórico exige sitemap. ClaimReview perdeu incentivo desde que o Google parou de exibi-lo (jun/2025) e a cobertura pode cair. Regex de título é por agência e quebra se o padrão editorial mudar, mas quebra de forma visível (veredito `None`), diferente de um seletor CSS que devolve menu.
- **Complemento:** com o corpus RSS, **7/7 paráfrases** acharam a checagem certa no rank 1. O gargalo era o corpus, não o BM25. Troque o piso absoluto por um piso relativo (ex.: score ≥ 1,5× a mediana da consulta) e adicione stemming RSLP. Embeddings ficam para depois do eval (seção 5).

#### A2. [Crítico] O crawler não extrai corpo, e a premissa do "schema por LLM" nunca foi implementada
- **Falha:** as 21 homepages têm `markdown_len=0` (`fit_markdown` vazio sem content filter). Em `output/`, 15/19 corpos têm menos de 200 caracteres. Em `output_checagem/`, os 17 são markdown de navegação. Quando o LLM local (LFM2.5-VL-3B) respondeu, alucinou: Virginia Fonseca virou "fotógrafa", Vini Jr. virou "ator". Os seletores de `br_news_crawler.py:150-583` são escritos à mão e nunca executados; nenhum código de runtime lê `css_selectors`, `rss`, `sitemap` ou `article_url_patterns`. Testados em páginas reais, os de aos-fatos, estadao e bbc extraem 0 caracteres.
- **Por quê (premissa):** "gerar um schema CSS por portal uma vez e reusar" é frágil mesmo se implementado. O layout muda, página de listagem ≠ página de artigo, há AMP e conteúdo renderizado por JS. E o sistema não tem como perceber quando o seletor passa a devolver lixo.
- **Correção escolhida:** aposentar o `br_news_crawler.py` como fonte do índice e adotar uma **cascata de extração** onde cada nível tem uma condição de falha explícita:
  `JSON-LD (ClaimReview/NewsArticle.articleBody)` → `trafilatura` → `seletor corpo do catálogo` → `ReaderLM-v2 com schema` → `falha explícita (metodo="falha")`.
- **Trade-offs:** mais uma dependência (trafilatura, determinística, na casa dos milissegundos). Perde-se a "descoberta automática de schema", que em compensação nunca funcionou. ReaderLM só entra no fim porque custa de 10 s a 2 min por página (seção 4).

#### A3. [Alto] A Lupa nunca tem o corpo lido; o catálogo casa nomes por substring
- **Falha:** o catálogo aponta `lupa.uol.com.br`, mas as matérias estão em `agencialupa.org`. `por_nome_fonte` marca `catalogada=True` por substring do nome, então a descoberta é pulada e `aprofundar` rejeita com "fora do catálogo" (confirmado ao vivo). O mesmo matching mapeia "Estadão Mato Grosso" para `estadao`, e `por_nome_fonte("")` devolve G1, o que faz todo resultado do Scholar aparecer como "G1".
- **Correção escolhida:** decidir `catalogada` pelo **domínio da URL** (eTLD+1), com uma lista de aliases por portal (`agencialupa.org`, `lupa.uol.com.br`). Nunca por nome de fonte.
- **Trade-offs:** os aliases precisam ser mantidos à mão, mas a lista é curta e verificável.

#### A4. [Médio] O extrator de runtime funciona; tetos e bloqueios estragam os casos grandes
- **Falha:** `extrair_corpo` deu de 2,4 mil a 4 mil caracteres em 13/13 páginas reais **com o HTML completo**. Mas o teto de 500 KB reduz a BBC (HTML de 9 MB) a 355 caracteres, que ainda contam como "corpo lido". O teto de 200 KB da descoberta derruba G1 e Estadão para 65–98 caracteres. UOL e TSE devolvem 403 para o UA `factcheck-mvp/0.1`. Entidades HTML não são decodificadas.
- **Correção escolhida:** teto de 5 MB, `articleBody` do JSON-LD antes da regex, UA de navegador, `html.unescape`, e **`corpo_lido=True` só se o corpo tiver ≥ 500 caracteres**.
- **Trade-offs:** mais memória e banda por página (irrelevante no experimento).

#### A5. [Médio] O catálogo "curado" se escreve sozinho
- **Falha:** `pipeline.py:410-411` chama `inserir_no_catalogo` direto, contrariando a docstring de `descoberta_site.py:7` ("propõe… fila de aprovação"). O diff local tem 15/35 portais automáticos, com nomes errados: um blog de hospital virou "Ações importantes que ajudam a combater a dengue". E ser catalogado dá **peso cheio** à fonte.
- **Correção escolhida:** gravar só em `catalogo_propostas.json`. Uma fonte descoberta nunca vira `catalogada` durante a consulta.
- **Trade-offs:** o catálogo cresce devagar, o que é exatamente o objetivo de um catálogo curado.

### B. Harness do agente de descoberta

#### B1. [Alto] O "agente" é um plano estático, e o crítico decide por proxy de URL
- **Falha:** `agente.py` não chama LLM. As "ondas" são reformulações por regex, e o crítico para diante de **qualquer** URL cujo host é de checagem, mesmo fora do tema (sonda S1). Com 3 afirmações e teto 8, a onda 2 **nunca roda** (S3). Quando roda, o crítico escolhe a sugestão com mais tokens novos, ou seja, a mais distante da afirmação (S2). A lista de hosts de checagem erra: `valorinveste` e `canalsaude` contam como checagem, e `agencialupa.org` fica de fora. O A/B (`scripts/ab_descoberta.py`) nunca produziu saída (`output_ab` não existe) e usa uma métrica circular, sem gold.
- **Por quê (design):** um loop de agente só tem valor se o passo seguinte depende do **conteúdo** do que foi encontrado. Aqui o crítico roda *antes* do juiz, então não sabe se a URL trata do tema. É um laço de controle sem sinal de erro.
- **Correção escolhida:** **inverter a ordem**. Primeiro busca determinística (1 query de palavras-chave + 1 `site:` das agências), depois dedupe e juiz. **Só então** o crítico, que recebe a saída do juiz ("0 fontes com postura", "só relatos sem endosso") e decide se gasta mais uma onda. A reformulação da query passa a ser feita por LLM, com a afirmação e as fontes já rejeitadas no prompt.
- **Trade-offs:** 1 a 2 chamadas de LLM a mais por consulta que precisar de segunda onda (a maioria não precisa), e mais latência nesse caso. Em troca, o loop passa a ter um sinal real. Uma alternativa mais simples e defensável é **remover o agente** e usar o modo `avancada` fixo até o eval mostrar que a segunda onda melhora o recall.

#### B2. [Médio] O estado do harness vaza entre consultas e desfaz o round-robin
- **Falha:** `buscas_usadas` lê um contador compartilhado, então requisições concorrentes consomem o teto umas das outras (S6). O dedupe atribui a URL só à primeira afirmação (S4). O corte global `[:12]` desfaz o round-robin por afirmação. Um timeout descarta todo o resultado parcial.
- **Correção escolhida:** estado por requisição (um `dataclass EstadoBusca` criado em `Pipeline.checar`), dedupe por URL canônica que **acumula** afirmações (`url → {afirmacoes}`), corte por afirmação e resultado parcial preservado em caso de timeout.
- **Trade-offs:** nenhum relevante.

### C. Prompts e comunicação entre etapas

#### C1. [Crítico] O fallback `lexico-top3` fabrica evidência
- **Falha:** `pipeline.py:463-472`. Sem juiz, as 3 primeiras fontes catalogadas recebem `{"sustenta": 0.35, "relevante": True}` sem que o conteúdo seja lido. Na sonda P3, páginas sobre dólar e futebol levaram a **BAIXA**, e a etapa registrou "3 relevante(s) após julgamento llm-juiz". Como o teto é de 50 chamadas/dia (~16 por consulta), **esse é o modo dominante** de operação.
- **Por quê:** um fallback que produz a *mesma forma* de dado que o caminho principal é indistinguível dele para as etapas seguintes. A degradação deixa de ser "graciosa" e passa a ser silenciosa.
- **Correção escolhida:** sem juiz, **não há nível**. A resposta vira `indeterminada` e a limitação diz "sem julgamento de conteúdo". Todo fallback grava `motor="fallback-*"` e o agregador **ignora** sinais com esse motor.
- **Trade-offs:** muito mais respostas "indeterminada" quando o LLM está fora. É a resposta honesta; a alternativa é um nível sem base.

#### C2. [Alto] O prompt do juiz não define `relevante` e o exemplo induz `true`
- **Falha:** `juiz_llm.py:33-38` pede `{"0": {"score": N, "relevante": true}, ...}`. O campo nunca é definido, e o exemplo traz o literal `true`. Em `juiz_llm.py:86-89`, o booleano passa por cima do score: `{"score": 0, "relevante": true}` conta como fonte relevante (sonda P2: 3 fontes com score 0 → BAIXA; P10: score −10 → BAIXA).
- **Correção escolhida:** o juiz classifica em **4 classes** (`SUSTENTA | REFUTA | RELATA_SEM_ENDOSSO | NAO_TRATA`), com **citação obrigatória** do trecho da fonte que justifica a classe. O código verifica que a citação existe literalmente no texto da fonte e rebaixa para `NAO_TRATA` se não existir. A relevância é *derivada* da classe, não pedida separadamente. Saída em JSON validada por Pydantic, com `response_format` quando o provedor aceitar.
- **Trade-offs:** mais tokens de saída (a citação) e alguns itens rebaixados por citação imperfeita (aceitar match fuzzy ≥ 0,9). Em troca, a alucinação do juiz fica detectável.

#### C3. [Alto] A arquitetura resumir→juiz joga fora o que decide
- **Falha:** quando há snippet, o título é descartado (`pipeline.py:444-445`). Na sonda P6, "**É falso que** ibuprofeno piora dengue" não chega ao resumidor. O resumidor recebe "sem dar veredito", instrução que tende a apagar justamente o veredito do checador (plausível, sem medição live). O juiz vê **só** o resumo: sem veículo, tipo de fonte, data ou selo.
- **Por quê:** cada etapa de LLM é uma compressão com perda. Aqui a compressão é instruída a remover o sinal mais importante.
- **Correção escolhida:** **eliminar o resumidor**. Uma chamada de juiz por lote, com itens estruturados: `{afirmacao, veiculo, tipo_fonte, data, titulo, selo_claimreview?, trecho_corpo[:3000]}`. O juiz vê título **e** corpo.
- **Trade-offs:** prompt maior por chamada, mas metade das chamadas (uma em vez de duas por fonte), o que também alivia o teto diário.

#### C4. [Médio] Extração de afirmações e montagem de queries
- **Falha:** um preâmbulo do LLM ("Aqui estão as afirmações:") vira afirmação e ocupa 1 das 3 vagas. Números iniciais são cortados ("30 mil" → "mil") pela regex de limpeza de marcadores. A query "avançada" chega a 48 palavras, acima do limite do Google, e inclui hosts amplos.
- **Correção escolhida:** afirmações em **JSON** (`[{"afirmacao": ..., "consulta": "5-8 palavras-chave"}]`). O LLM já produz a consulta de busca junto com a afirmação, e a regex de limpeza desaparece.
- **Trade-offs:** depende do LLM seguir o JSON. Manter o fallback determinístico atual, marcado como `fallback`.

#### C5. [Baixo] Padrões: "NENHUM" vira sinal de desinformação
- **Falha:** `padroes_llm.py:98` tem um `return []` onde a função devia devolver uma tupla. Quando o LLM responde "NENHUM", o regex de fallback dispara `sem_data_local`, que entra como sinal de desinformação.
- **Correção escolhida:** corrigir o retorno e tratar "NENHUM" como lista vazia válida. Mover padrões de estilo para fora do nível (ver D4).

### D. Decisão e agregação

#### D1. [Crítico] "Cobertura ampla" reduz a propensão mesmo quando as fontes refutam
- **Falha:** `pipeline.py:572-575` cria "cobertura ampla" quando `contagem["n"] >= 3`, sem olhar a postura. `agregador.py:64-70` dá −0,5 a "ampla" sem veredito explícito. As mesmas 3 fontes geram "refutam" (+1) **e** "ampla" (−0,5). Resultado: três refutações dão BAIXA (+0,227).
- **Por quê:** a corroboração conta **menções**, não **concordância**. Uma notícia "É falso que café cura câncer" menciona a afirmação e entra como cobertura.
- **Correção escolhida:** a corroboração passa a ser **por postura**: `n_independentes_sustentam` e `n_independentes_refutam`, calculados só sobre as classes do juiz (C2). "Cobertura ampla" deixa de existir como sinal próprio.
- **Trade-offs:** depende do juiz, e sem juiz não há corroboração (coerente com C1).

#### D2. [Crítico] Faixas assimétricas: neutro e ausência de evidência viram "baixa"
- **Falha:** `agregador.py:100-107`. `indeterminada` só vale se `|score| < 0,15` **e** `den < 0,5`. Com score 0 e massa de pesos ≥ 0,5, o resultado é BAIXA. No intervalo [−1, 1], baixa ocupa 67,5%, média 15% e alta 17,5%. Somar um sinal neutro (BERTimbau em 0,45) a um empate muda o resultado de INDETERMINADA para BAIXA. Um sinal isolado satura: o modelo em 0,61 sozinho dá ALTA.
- **Correção escolhida:** **log-odds** a partir de um prior de 0,5, com faixas **simétricas** em torno de 0. `indeterminada` depende de **quantidade de evidência de conteúdo** (0 fontes com postura → indeterminada), não da massa de pesos. Sinais de direção 0 não entram no denominador.
- **Trade-offs:** os limiares mudam, e os testes que travaram o comportamento atual quebram, como devem. Os pesos continuam sendo priors até existir eval.

#### D3. [Crítico] Direção do selo por substring de texto com o nome do portal
- **Falha:** `agregador.py:57,62-63` procura "fake"/"falso"/"fato" em `rotulo + valor`, e o rótulo inclui o nome do portal (`pipeline.py:268`). Sonda P4, mesmo selo e mesma checagem: VERDADEIRO do "Fato ou **Fake**" dá ALTA e do "Lupa" dá BAIXA; DISTORCIDO do Aos Fatos e BOATO do "Fato ou **Boato**" dão BAIXA. EXAGERADO, INSUSTENTÁVEL e FARSA ficam com direção 0.
- **Correção escolhida:** o selo vira um **campo tipado** (`veredito_normalizado: FALSO|ENGANOSO|VERDADEIRO|…`), mapeado por uma tabela explícita `selo_da_agencia → enum` (ex.: "Distorcido" → ENGANOSO). A direção sai do enum, nunca de texto livre.
- **Trade-offs:** a tabela precisa ser mantida por agência (~40 rótulos no total). Selo desconhecido vira `None` + aviso, nunca direção inventada.

#### D4. [Crítico] O BERTimbau/FakeBR mede tema e estilo, não veracidade
- **Falha (sondas com o modelo real):** o modelo é insensível à negação: "É falso que a vacina altera o DNA" dá 0,957, e o boato em si dá 0,947. "Café **não** cura câncer" dá 0,873, mais que "Café cura câncer" (0,625). A mesma frase passa de 0,268 para 0,939 só por estar em minúsculas. O peso efetivo é 0,17, quase o mesmo de "refutam" (0,20), que resume N fontes. A calibração Platt foi feita no FakeBR (artigos longos) e não transfere para títulos curtos, e o `threshold_val_opt` de 0,42 é ignorado. `config.json` não tem `id2label`: a suposição `logit[1]=fake` é coerente com as sondas, mas não verificável (plausível).
- **Por quê:** FakeBR rotula artigos inteiros por fonte e estilo. Uma afirmação de 10 palavras não carrega esse sinal, e o modelo aprende atalhos (tema saúde/política, caixa, pontuação).
- **Correção escolhida:** **tirar o modelo do cálculo do nível**. Ele continua no recibo como "sinais de estilo do texto (não indica veracidade)", ao lado dos padrões de linguagem. O nível passa a ser só **veracidade**: selo normalizado + postura das fontes independentes.
- **Trade-offs:** sem fontes, o sistema responde `indeterminada` em vez de "chutar" com o modelo. Para textos longos colados pelo usuário o modelo tem algum valor, e aí pode voltar como sinal separado, depois de um eval que mostre ganho.

#### D5. [Alto] Dupla contagem e independência mal medida
- **Falha:** a mesma URL entra pelo índice e pela web e é contada 2× (P4). Duas páginas da mesma Lupa geram "5 indícios elevam": selo ×2, laya ×2 e uma "convergência" de um único checador. Já 10 veículos refutando geram 1 sinal só. `corroboracao.py` recebe `model_dump()` sem corpo e compara só títulos: Estadão Conteúdo com corpo idêntico em 4 domínios conta 4×. A divergência de data nunca dispara porque o código lê `data_publicacao` e o schema tem `data_pub` (`corroboracao.py:74` vs `schemas.py:46`).
- **Correção escolhida:** **dedupe canônico global antes do juiz** (URL normalizada + hash de shingles do corpo). Independência = cluster por (domínio raiz, similaridade de corpo ≥ 0,8, assinatura de agência "Estadão Conteúdo/Agência Brasil/Reuters"). **Um voto por cluster**, e um sinal por direção (não um por peça). Corrigir o nome do campo de data.
- **Trade-offs:** a detecção de agência por assinatura é uma heurística. Falsos negativos voltam a contar republicações, mas o erro fica limitado ao número de clusters.

#### D6. [Alto] As travas do pipeline anulam o agregador
- **Falha:** `pipeline.py:614-643` sobrescreve o nível em vários casos, o que faz a decisão real ser uma árvore implícita espalhada. Na mutação, com `PESOS` todos em 0,01 ou invertidos, 43/43 testes passam. Os pesos somam 1,05 e `PESOS["llm-juiz"]` é código morto. O "why" chega a dizer "Propensão alta" enquanto o header diz INDETERMINADA, e diz "0 fonte(s) consultada(s)" quando 3 foram lidas e julgadas fora do tema.
- **Correção escolhida:** **uma única função de decisão** (`decidir(evidencias) -> Decisao`) pura e testável. O "why" é gerado a partir do mesmo objeto `Decisao`, então não pode divergir do nível.
- **Trade-offs:** refatoração que toca pipeline, agregador e testes.

### E. Testes e avaliação

#### E1. [Alto] Os testes verificam o encanamento, não o comportamento
- **Falha:** `test_regressao_36` só confere se a propensão é um dos 4 valores do enum. `test_agente.py:41-54` trata a parada prematura do crítico como desejada. Os testes do juiz mockam `resumir` e `termometro`. `implicacao.implicacao()` é testada, mas não é usada pelo pipeline. Nenhum teste passa pelo caminho web com fontes que refutam. Não existe eval rotulado, então não há como saber se uma mudança melhora algo.
- **Correção escolhida:** **harness de eval com replay**, *antes* de qualquer ajuste de pesos (seção 5).
- **Trade-offs:** 150–300 afirmações rotuladas é trabalho de curadoria, mas o ClaimReview fornece rótulos humanos de graça (A1).

---

## 4. ReaderLM-v2 (`mradermacher/ReaderLM-v2-GGUF`): onde encaixa

**Estado na sua máquina:** o Unsloth Studio (porta 8888) já lista o modelo, e `ReaderLM-v2.Q8_0.gguf` (1,8 GB) está no cache do HF. M4 com 24 GB: cabe com folga. Não existe `unsloth/ReaderLM-v2-GGUF`; a versão "do unsloth" é esse GGUF servido pelo Unsloth.

**O que é:** Qwen2.5-1.5B ajustado para HTML→Markdown e HTML→JSON com schema. Contexto de 512K tokens, 29 idiomas, português incluído. Licença **CC-BY-NC-4.0**: serve para o experimento, não para produto comercial sem acordo com a Jina.

**Decisão: usar só como penúltimo nível da cascata**, para páginas sem ClaimReview (G1 Fato ou Fake, reportagens do Aos Fatos, sites regionais) quando trafilatura/seletor não resolvem. Nesse caso ele extrai, com schema, `afirmacao_checada`, `veredito` (selo literal) e `trecho_evidencia`.

| | JSON-LD ClaimReview | trafilatura | ReaderLM-v2 |
|---|---|---|---|
| Custo | instantâneo | ms | 10 s–2 min/página no M4 (estimado) |
| Veredito | oficial do editor | não | sim, pode alucinar |
| Cobertura | só onde o site publica | universal | universal |

**Onde NÃO usar:**
- **Gerar seletores CSS.** Não é um modelo de instrução geral, e a própria Jina diz que instrução livre é fraca.
- **Corpo de notícia comum.** trafilatura resolve (F1 0,945 no benchmark ScrapingHub).
- **Classificar veredito no enum.** Use a tabela de D3.
- **Páginas que já têm ClaimReview.**

**Pegadinhas que quebram a extração se ignoradas:**
- O `generation_config.json` do repo usa temperature 0,65. Mande `temperature: 0` e `repeat_penalty: 1.08` explicitamente em toda requisição.
- O template ChatML está embutido no GGUF e injeta o system sozinho. Mande só a mensagem `user`, com o prompt exato do model card (em [r4_readerlm.md](r4_readerlm.md)).
- Sirva com `-np 1` (com os 4 slots padrão, cada um recebe só 1/4 do contexto e o HTML é truncado) e fixe o contexto (o padrão de 512K usa ~15 GB de KV cache).
- `finish_reason == "length"` = falha (loop de repetição ou truncamento).
- O JSON devolve `"Unknown"` em vez de `null`, e ~2% das saídas não são JSON válido. Valide com Pydantic e **verifique que cada campo aparece no texto da página** (≥ 80% das palavras). O que não passar é descartado como alucinação.
- **Recorte o HTML antes** pelo seletor `corpo` do catálogo + `h1`. Isso reduz os tokens de 10 a 50×. A página do G1 tem 1,1 MB bruto e fica em ~35k tokens só depois de limpeza agressiva.

```bash
unsloth run -hf mradermacher/ReaderLM-v2-GGUF:Q8_0 --max-seq-length 65536 -np 1 --temperature 0 --repetition-penalty 1.08 --disable-tools --api-only -p 8889
```

Protótipo da cascata: [cascata_readerlm.py](cascata_readerlm.py). A etapa JSON-LD foi testada no Estadão Verifica (`veredito='Falso'`, autor e data corretos). A etapa ReaderLM **não foi executada**, para não trocar o modelo carregado no seu Studio.

**Experimento sugerido (sem rotular nada à mão):** ~30 URLs com ClaimReview. Use o ClaimReview como gabarito e meça a acurácia do ReaderLM **escondendo o JSON-LD** da entrada. Compare Q8_0 / Q6_K / Q4_K_M e com/sem recorte por seletor.

---

## 5. Arquitetura corrigida

```
[afirmações JSON {afirmacao, consulta}]
      │
      ├─► [índice ClaimReview/RSS] ──────────────────────────────┐   selo normalizado (enum)
      │                                                          │
      └─► [busca determinística: consulta + site:agências]       │
                 │                                               │
           [dedupe canônico + clusters de independência]         │
                 │                                               │
           [extração em cascata: JSON-LD → trafilatura → ReaderLM → falha]
                 │                                               │
           [juiz 1 chamada: SUSTENTA|REFUTA|RELATA|NAO_TRATA + citação verificada]
                 │                                               │
           [crítico: 0 posturas? → 1 onda extra com query reescrita pelo LLM]
                 │                                               │
           [decidir(): log-odds simétrico, 1 voto por cluster] ◄─┘
                 │
           nível + why gerado do mesmo objeto Decisao
           (estilo/BERTimbau/padrões: exibidos à parte, fora do nível)
```

**Ordem de implementação** (ganho por esforço; cada passo verificado pelo eval do passo 0):

| # | Passo | Resolve |
|---|---|---|
| 0 | **Eval primeiro:** `eval/claims_ptbr.jsonl` (rótulos do ClaimReview + 12 casos das sondas + pares adversariais de negação, relato e caixa), snapshots de busca congelados para replay offline, métrica de **erro grave** (falso→baixa, verdadeiro→alta) como gate | E1 |
| 1 | Ingestor RSS + ClaimReview; piso BM25 relativo + RSLP | A1 |
| 2 | Remover `lexico-top3`; sem juiz → indeterminada; motor `fallback-*` ignorado | C1 |
| 3 | Selo tipado + tabela por agência; corroboração por postura; remover "cobertura ampla"; faixas simétricas; `decidir()` única | D1, D2, D3, D6 |
| 4 | Juiz 4 classes com citação verificada, sem resumidor | C2, C3 |
| 5 | Dedupe canônico + clusters; domínio + aliases no catálogo; catálogo só por proposta | D5, A3, A5 |
| 6 | Cascata de extração (trafilatura, tetos, UA, ≥500 chars); ReaderLM no fim | A2, A4 |
| 7 | Agente só depois do juiz, **se** o eval mostrar ganho de recall na 2ª onda | B1, B2 |
| 8 | BERTimbau e padrões fora do nível | D4, C5 |

Os passos 2 e 3 sozinhos já eliminam os casos "refutado → BAIXA" das sondas. O passo 1 é o que dá ao sistema algo para achar.

---

## 6. Fora do escopo, mas vale saber

- `bot.log` (local, fora do git por `.gitignore`) contém o token do Telegram e a chave da SerpAPI dentro das URLs de requisição logadas. Os valores não foram reproduzidos em nenhum relatório. Vale rotacionar as duas chaves e baixar o nível de log do `httpx`.
- Todos os logs da raiz são de 21/09, anteriores ao juiz (loop 12) e ao agente (`d40c812`, 28/09): **não existe execução registrada do fluxo atual**. O PROGRESSO.md descreve comportamento que ninguém observou ao vivo desde então.
