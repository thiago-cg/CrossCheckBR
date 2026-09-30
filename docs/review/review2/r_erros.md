# Análise de erros — Iteração 2 (70 casos dev, juiz deepseek-v4.1-flash)

Fonte: os 70 runs de `validos.json`, reprocessados sem nenhuma chamada live. Scripts em `scratchpad/review2/`:
`extrair.py` gera `casos_extraidos.json` a partir de trace, cassetes SerpAPI e eventos. `dossie2.py` monta os
dossiês por caso (`dBC2.txt`, `dF.txt`, `dG.txt`). `reconstruir.py` refaz a entrada de `decidir()` e **reproduz 69/69
níveis e L exatos**. `simular*.py` testa as regras alternativas. `onda2.py` mede o efeito da onda 2.
Legenda: **[C]** = confirmado no trace/cassete; **[P]** = plausível, não dá para provar offline.

Reclassifiquei os 50 erros com regras explícitas. B+C somam 24, igual às 18+6 da Iteração 2, mas a divisão
interna muda: 6 casos "só fora do tema" e 18 "com relatos". Contei F=19 porque incluí 2 verdadeiro→média.
G ficou com 3 graves; xi_jinping e eua_prender, sem_evidencia→alta, estão numa categoria à parte.

---

## 1. Tabela por causa, com subcausas quantificadas

| causa | n | subcausa (etapa) | n | casos |
|---|---|---|---|---|
| **B+C: o assunto certo não chegou ao juiz** | 24 | (ii) a checagem real veio no Google e foi descartada depois | **0** [C] | nenhum. A URL da checagem (ou uma com ≥40% do slug) não aparece em nenhum resultado dos 24 |
| | | tema vizinho achado, checagem não veio; o juiz marcou RELATA corretamente | 8 | cafe_cura_cancer ×2, cafe_nao_cura, janja, flavio_impeachment_dino, macete_urna, lula_salario_3mil, gilmar |
| | | (iii) Google devolveu ruído, casando por uma palavra solta, para uma consulta razoável | 7 | comicio ("Porto Seguro", "Porto de Santos"), moto_placa ("usar" → Supabase/Asana), flavio_fugiu (AG: "bebê fugiu"), chips_taiwan (PDFs), radar (drones na Ucrânia), michelle (AG: páginas do WhatsApp), ibuprofeno_cura (farmácias) |
| | | (i) extração/consulta perdeu a entidade decisiva | 5 | daniela_lima e sacani (atribuição removida), dark_horse ("PF concluiu" virou polaridade `nega` sobre "teve corrupção"), bolsa_familia (comparação partida em 2 afirmações), rick (só "Rick", sem a dupla) |
| | | execução: SerpAPI ReadTimeout de 25 s sem retry | 4 (+2 secundários) | china_acai (as 2 buscas da onda 1), toyota, whatsapp, medico_robo (busca simples perdida); também afetou bolsa_familia e gilmar |
| | | busca de agências (13 `site:` com OR) trouxe checagem relevante | **0 de 24** [C] | — |
| **F: média** | 19 | F1: só votos de rede social, só título (0,42 cada; no máximo 0,84 < τ=1,10) | 11 | relato_cafe, romario, evangelicos, nikolas, lula_janja_7bi, voto_prova_vida*, miojo, cr7, rebeca_pato, vaca_trem*, stf_idade |
| | | F2: um único site não curado refuta (0,42–0,60); dois deles republicam o G1 | 3 | camiseta_nordestinos (agorario = G1), virginia (FB do g1 + guiacidades = G1), el_nino |
| | | F3: **havia agência refutando**, mas ela perdeu nos votos ou pesou pouco | 5 | cucurella, estribo, semaforos, fachin, codigo_fonte |
| **G: grave** | 3 | espalhadores em rede social votam SUSTENTA (decisão) | 1 | chico_cesar |
| | | extração tirou "o JN anunciou" + selo ClaimReview falso do SBT + nenhuma noção de data | 1 | jn_soltura_vorcaro |
| | | rótulo provavelmente errado (a reescrita manual perdeu o que era falso) | 1 | ibge_trafico_no_pib |
| **S: sem_evidencia→alta** | 2 | política da métrica: "não há confirmação" vira REFUTA e leva a alta | 2 | xi_jinping_avc, eua_prender_lula |
| **E** | 1 | a trava `VAGO_RE` disparou em "piora o quadro" | 1 | ibuprofeno_piora_dengue (L=−2,04 daria **baixa**, que é o acerto) |
| **A** | 1 | juiz pendurado até o timeout de 600 s do eval | 1 | oreo |

\* verdadeiro→média.

**Acertos frágeis [C]:** 2 dos 20 acertos são por acaso.
- `cr_lula_garis_nao_da_orgulho`: o núcleo virou "Ser gari é profissão que dá orgulho" com polaridade `nega`. Posts dizendo que gari tem orgulho deram SUSTENTA, e daí saiu alta. A fala do Lula nunca foi avaliada.
- `cr_lula_nao_acredita_em_deus`: a alta vem do selo falso do SBT (+1,2).

---

## 2. Respostas às perguntas

### 2.1 B+C (24): a busca não encontrou as checagens
- **(ii) é 0 [C].** Nos 70 casos, a URL da checagem real apareceu nos resultados só 4 vezes (hytalo, cucurella,
  garis, xi). Em 3 delas o juiz marcou REFUTA; em garis marcou RELATA, o que é correto dado o núcleo errado.
  Republicações do G1 (agorario, sauipefm, conexao067) também receberam REFUTA. **O juiz nunca descartou uma
  checagem de verdade.** O que existe são vizinhos: páginas de listagem ou barra lateral das agências cujo snippet
  cita o título da checagem, por exemplo `medico_robo`, com sidebars do e-farsas dizendo "Médico-robô é flagrado
  fugindo do hospital! Real ou fake?". Ali RELATA(título) está certo; faltou abrir o link.
- **A consulta de agências (13 `site:` com OR) não funciona [C].** Foram 70 buscas e 377 resultados. Desses, 164
  são home, `/page/N`, `/author/`, `/sobre` ou tag; 12 buscas vieram vazias; **só 3 resultados (0,8%) eram artigos
  do tema**. A cassete do Toyota mostra `total_results: 2.070.000.000` e como resultado "Brasil – Wikipédia",
  "@brasil": o Google ignorou os `site:`. Essas páginas ocupam **162 de 962 vagas do juiz (17%)**, com 134
  NAO_TRATA. Recebem bônus em `_selecionar` (+0,15 curada, +0,1 checagem) e por isso tiram do juiz resultados
  do tema. Exemplo: em ibge, 4 páginas aleatórias do e-farsas entraram e o post do FB/IG "O IBGE vai incluir…"
  ficou de fora. Mesmo assim, essa busca foi decisiva em xi e eua_prender (boatos REFUTA) e em estribo (listagem
  do Aos Fatos). Dá para substituir, não para cortar.
- **A busca simples também traz ruído [C].** Das 111 buscas simples, só 53% dos resultados em média têm título ou
  snippet do tema, e 33 buscas têm ≤25% de resultados do tema. Das 16 buscas com topicalidade zero, **10 são da
  onda 2 com "checagem", "é falso" ou "boato" na consulta** [C]. A reformulação também inventou entidades:
  "RENSERG … garimpeiros" em radar deu 0 resultados; "Senado" em flavio_impeachment trouxe o portal do Senado.
- **(iii) "não indexado" não é verificável offline [P].** As checagens têm de 1 a 10 semanas e boatos, e-farsas
  e g1 são bem indexados. O mais provável é problema de ranqueamento com essas consultas e parâmetros
  (`num=10`, `gl=br`, 6 a 8 palavras), não ausência de índice.

### 2.2 F (19): por que ficou em média, e simulações offline
Faixas: |L| < τ = ln 3 = 1,10. Voto de rede social só com título vale 0,6 × 0,7 = 0,42. Um site curado com corpo
vale 1,0. Uma checagem de agência com postura, sem selo tipado, vale **1,0 < τ**. Na prática, uma agência sozinha
nunca produz alta.

| caso | L | votos (tipo) |
|---|---|---|
| cucurella | −0,86 | G1 fato-ou-fake **+1,0** vs reddit −0,42, vietnam.vn −0,60, tiktok −0,42, instagram −0,42 (espalhadores) |
| estribo | +0,40 | aosfatos **+1,0** (listagem), sauipefm (=G1) +0,42 vs areavip −0,60, facebook −0,42 |
| semaforos | 0,00 | **checamos.afp.com +0,42** (AFP **fora do catálogo**, logo "não curada") vs instagram −0,42 |
| fachin | +0,70 | UOL Confere +0,70 (só título; a checagem está no snippet da sidebar de outras páginas do UOL) |
| codigo_fonte | +0,40 | Lupa +1,0 (fala do Delgatti) vs Gazeta −0,60 ("TSE alterou código-fonte após CGU", verdade literal) |
| camiseta / virginia / el_nino | +0,60 / +0,84 / +0,60 | republicação do G1 ou site de previsão, 1 cluster não curado |
| 11 casos F1 | ±0,42 a ±0,84 | só instagram, facebook, threads, tiktok. Em 5 casos falsos o voto é SUSTENTA de quem espalha o boato (evangelicos, nikolas, rebeca, stf, romario) |

**Simulação offline** sobre os 70 casos, com `decidir()` real sobre as evidências gravadas (baseline reproduzida:
20 acertos / 37 com parcial / 3 graves / 27 indeterminadas):

| regra | acerto | +parcial | graves | indet. | efeito colateral |
|---|---|---|---|---|---|
| R1: agência REFUTA vale como selo (w=1,5) | 21 | 37 | 3 | 27 | só fachin muda |
| R11: se uma agência refuta a afirmação, SUSTENTA de fontes que não são agência não vota | 21 | 37 | 3 | 27 | estribo +1 |
| R2: SUSTENTA de rede social não vota | 21 | 33 | 2 | 34 | 7 médias viram indeterminada (perde parcial) |
| **R2b: SUSTENTA de rede social = 1 cluster só** | 20 | 38 | 2 | 27 | chico_cesar vira média; garis perde o acerto por acaso |
| R3: rede social nunca vota | 14 | 28 | 3 | 39 | **cria grave** (bets) — descartar |
| R3b: toda rede social = 1 cluster | 18 | 36 | 3 | 28 | **cria grave** (bets) — descartar |
| R8: selo de página só de publisher checador | 20 | 38 | 2 | 27 | corrige jn_soltura; expõe o acerto por acaso de deus |
| R6: τ = ln 2 | 25 | 37 | **4** | 27 | **cria grave** (cucurella→baixa) sozinha |
| R1+R11+R8 | 25 | 38 | 2 | 27 | |
| **R1+R11+R2b+R8** | **25** | **39** | **1** | 27 | 9 mudanças, 1 regressão (deus: alta→média, que era acerto por acaso) |
| **+ correção do VAGO** | **26** | **40** | **1** | **26** | +ibuprofeno_piora (baixa) |
| R1+R2+R8+R6 | 28 | 34 | 1 | 34 | ganha acerto trocando parcial por indeterminada; mais arriscado |

Grave restante com qualquer regra: `cr_ibge_trafico_no_pib`, que é problema de rótulo (2.4).
Ressalva de overfitting: desenhei as regras depois de ler estes casos e não há traces de holdout em `runs/`.
R1, R11, R2b e R8 têm razão geral (checagem > boato repetido; selo precisa ser de checador; repetição em rede
social não é confirmação independente). τ = ln 2 não tem. **Validar no holdout antes de adotar.**

### 2.3 C: o juiz é conservador demais com checagens?
**Não [C].** Das 242 respostas RELATA do juiz, 17 citam palavras de desmentido em `pagina_diz`, e quase todas
estão certas:
- boatos.org/page/71: "noticia que circula o boato de que Lula está com doença grave…" é outra alegação;
- e-farsas sidebar: "pergunta se é real ou fake, sem concluir" (só tem o título);
- Comprova: "lista boatos eleitorais… sem tratar do suposto macete".

Os casos discutíveis são poucos. Em virginia, um Instagram "reproduz conteúdo atribuído ao g1 segundo o qual
seriam falsas…" recebeu RELATA. Em xi, um IG com "não há confirmação oficial" recebeu RELATA, enquanto outros IGs
com o mesmo texto receberam REFUTA. É inconsistência entre lotes, não viés. **O C não é culpa do juiz: as páginas
de checagem não chegaram até ele.**

### 2.4 G: traces completos
- **chico_cesar** (`20260930-095619-afd24a`): a busca de agências veio vazia. A busca simples trouxe 8 posts de
  IG/FB/TikTok, todos lidos só pelo título. O juiz marcou SUSTENTA, por exemplo em "Chico cesar proíbe pessoas de
  direita de ouvir suas musicas kkkkkkk". Foram 3 clusters (instagram, facebook, tiktok) de −0,42, somando
  L=−1,26 ≤ −τ, logo **baixa**. Etapa culpada: **decisão**, porque espalhador conta como confirmação. A falta de
  busca é a causa de fundo. R2b leva a média.
- **jn_soltura_vorcaro** (`…095653-b584e0`): a extração tirou "o Jornal Nacional anunciou" e ficou "Vorcaro foi
  solto", polaridade afirma. Os votos: G1/JN de **nov/2025** "Justiça solta dono do Banco Master" SUSTENTA −1,0;
  **SBT News com ClaimReview de template** VERDADEIRO −1,2, embora o juiz tenha dito RELATA; IG −0,42; Revista
  Oeste "permanece preso" REFUTA +0,6. Resultado: L=−2,02, **baixa**. Confirmei na cassete: todo artigo do SBT traz
  `{"@type":"ClaimReview","claimReviewed":"Alegação revisada","reviewRating":{"alternateName":"Verdadeiro"}}`.
  Etapas culpadas: **extração**, **selo**, e ausência de data no juiz e na decisão (o selo da checagem era "antigo").
- **ibge_trafico_no_pib** (`…095947-66cce1`): 3 sites não curados SUSTENTA (−0,6 cada), incluindo
  "jornaldeminas … entenda-por-que-**g1**", que diz "o IBGE informou em nota que incluirá…". L=−1,8, baixa. O
  trecho da checagem em `data/checagens_holdout_excluidas.json` mostra que o e-farsas checou outra coisa: "o
  **Governo Federal teria ordenado** … incluir tráfico **e BETs** … para **inflar** os números". A entrada reescrita
  à mão perdeu isso. **Etapa culpada: o rótulo do caso** [P forte]. Pelas regras do projeto, cabe corrigir com
  `nota` (enganoso) ou reescrever a entrada.
- **xi_jinping / eua_prender** (sem_evidencia→alta): polaridade e juiz corretos; o boatos.org diz "não há
  confirmação" e o juiz marca REFUTA. É uma questão de **política da métrica**: para boato sem evidência,
  "propensão alta" é defensável. Decisão do dono do projeto, não bug.

### 2.5 A: execução
- **oreo** (`…100124-c1121c`): a 1ª chamada do juiz levou **106,6 s**. O item 1, a "Nota-Oreo.pdf" da Lupa, veio
  REFUTA ("Nega que haja petróleo ou hexano"). A 2ª chamada não voltou, e o eval matou o caso aos 600 s
  (`CancelledError`). Mecanismo [C, código]:
  - `llm.chat_json` passa `LLM_TIMEOUT_S=240` ao OpenRouter e sobrepõe `OPENROUTER_TIMEOUT_S=30`;
  - são 2 tentativas × até 3 modelos (juiz, geral, fallback);
  - `JUIZ_TIMEOUT_TOTAL_S=600` é igual ao `--timeout-caso 600`, então o teto do próprio pipeline nunca dispara antes.

  Com timeout adequado, o caso provavelmente sairia em média ou alta [P].
- **china_acai** (`…100432-c290f1`): as 2 buscas da onda 1 deram ReadTimeout de 25 s. Sem nenhuma página, a
  reformulação criou "exportação açaí Amapá China contrato 2031 checagem", que trouxe páginas genéricas sobre
  exportação.

### 2.6 Padrões transversais
- **Latência [C, amostra mista record/replay].** Soma de 3.527 s em 70 casos; p50 27 s, p95 117 s.

  | etapa | tempo | % |
  |---|---|---|
  | juiz | 1.352 s | 38% |
  | ondas extras | 654 s | 19% |
  | buscas da onda 1 | 480 s (355 s são **15 timeouts de 25 s da SerpAPI**, em 11 casos) | 14% |
  | deep crawl | 316 s | 9% |
  | descoberta-catalogo (não afeta o nível e roda síncrona) | 131 s | 4% |

  Só oreo e virginia somam **32% do tempo total**; virginia teve uma chamada de juiz de **441 s**. Por chamada
  live, o juiz tem p50 7,2 s, p95 46 s, e 10 de 112 chamadas passam de 30 s. `JUIZ_CONCORRENCIA` existe em
  `config.py`, mas **nenhum código o usa**: os lotes de 5 rodam em série, com 3,4 chamadas por caso.
- **Onda 2 [C].** Rodou em 47/70 casos e mudou o nível em 11:
  - 5 viraram acerto, entre eles bets, de grave para alta;
  - 6 foram de indeterminada para média (parcial);
  - 36 não mudaram.

  Custa 1 busca e 1 a 2 chamadas de juiz. Das consultas reformuladas, 40 de 47 têm "checagem", "boato" ou
  "é falso"; nelas só 5,5% das páginas julgadas tiveram postura, contra 15% nas outras 7 [P, amostra pequena].
- **Páginas sem corpo por domínio [C].**

  | domínio | tentativas de crawl | com corpo |
  |---|---|---|
  | Instagram/Facebook | 264 | **0** |
  | PDF/repositório | 66 | 0 |
  | TikTok/Threads/X/Reddit | 41 | 2 |
  | vídeo | 18 | 1 |
  | agências | 171 | 78% |
  | outros | 393 | 75% |

  Cerca de 40% das tentativas de crawl vão para domínios que nunca rendem corpo. Mesmo assim, IG e FB deram
  33 SUSTENTA e 29 REFUTA julgados só pelo título.
- **Listagens e tags das agências [C].** 75 páginas de listagem chegaram ao juiz em 38 casos: 61 NAO_TRATA,
  8 RELATA, 6 REFUTA. Várias trazem no snippet o título exato da checagem (estribo, fachin, daniela).
- **Afirmações partidas e atribuição removida [C].**
  - A regra 3 do prompt de `afirmacoes.py` diz: "Relato ('Fulano diz que X')… a afirmação verificável é X". Isso
    **apaga o autor em alegações de fala inventada**: daniela_lima, sacani, romario, nikolas, cr7, el_nino,
    jn_soltura. Em 3 dos 5 casos com `nega`, a negação estava **dentro da fala citada** e foi lida como negação
    do usuário (garis, deus, dark_horse), porque `_NEGACAO.search(texto)` aceita qualquer "não".
  - 4 casos foram partidos em 2 afirmações. Em 2 isso destruiu o sentido: bolsa_familia perdeu a comparação
    15% × 3,9%; codigo_fonte ficou com "original Alexandre de Moraes" como consulta.

---

## 3. Os 10 achados mais importantes

**1. A busca de agências é ruído e ainda tira vagas do juiz [C].**
- Evidência: 377 resultados, 3 do tema (0,8%), 164 home ou listagem; 162/962 vagas do juiz (17%), 134 NAO_TRATA.
  Em `…095947-66cce1` (ibge), 4 páginas aleatórias do e-farsas entraram no juiz.
- Mecanismo: o Google ignora `(site:A OR … 13 sites)`, e `_selecionar` dá bônus a curada e checagem.
- Correção:
  - (a) substituir por buscas na **busca interna das agências** (quase todas WordPress: `/?s=`, `wp-json/wp/v2/search`) via `replay.http_get`, sem custo de SerpAPI;
  - (b) ou 2 a 3 buscas com `site:` de **um** domínio cada, rodando entre boatos, g1/fato-ou-fake, lupa e aosfatos;
  - (c) cortar `/page/N`, `?paged=`, `/author/`, `/tag/` e `formato=` antes do juiz, e tirar o bônus de "curada" quando não há sobreposição de termos.
- Impacto [P]: é o maior potencial sobre B+C (24) e F3 (5); só dá para medir em record.
- Custo: médio. Risco: baixo. Com (c), são +8 a 17% de vagas úteis no juiz.

**2. Quem espalha o boato em rede social conta como confirmação independente [C].**
- Evidência:
  - chico_cesar (grave): 3 clusters sociais SUSTENTA;
  - cucurella: G1 +1,0 perde para 4 espalhadores;
  - estribo, semaforos: o mesmo padrão.
- Correção R2b: SUSTENTA de rede social vai para 1 cluster único e vale no máximo 1 voto de 0,42; REFUTA mantém.
- Impacto (simulado): graves 3→2 sozinho. Com R1, R11 e R8: acerto 20→25, +parcial 37→39, graves 3→1.
- Custo: baixo (lista de domínios em `decisao` ou `corroboracao`). Risco: baixo. R3 e R3b criam grave e foram descartadas.

**3. ClaimReview de template do SBT News vira selo VERDADEIRO [C].**
- Evidência: cassete da página do SBT (`claimReviewed:"Alegação revisada"`, "Verdadeiro" em todo artigo); 3 casos,
  aplicado em 2 (jn_soltura grave; deus, acerto por acaso).
- Correção R8: aceitar `veredito_pagina` só de publisher `tipo=checagem` do catálogo, ou com `claimReviewed`
  diferente de genérico e diferente do headline.
- Impacto: 1 grave a menos (jn vira média, que é acerto para enganoso) e 1 acerto por acaso a menos.
- Custo: muito baixo (`jsonld.veredito_da_pagina`). Risco: nulo.

**4. A extração apaga o autor da fala ("Fulano disse que X" → X) e inverte negação citada [C].**
- Evidência: 10 casos (daniela, sacani, romario, nikolas, cr7, el_nino, jn, garis, deus, dark_horse). 7 são erros
  e 2 são acertos por acaso. Causa: a regra 3 do prompt e o `_NEGACAO.search(texto)` em `_via_llm`.
- Correção:
  - quando o autor é pessoa ou veículo nomeado, o núcleo mantém "Fulano disse/anunciou X" e a consulta inclui o nome;
  - a regra 3 fica só para relato genérico ("dizem que", "circula vídeo");
  - negação dentro de "disse que…" não vira `nega`.
- Impacto [P]: 5 a 7 casos, dependendo da busca. **Exige record** (prompt novo = cassetes novas).
- Custo: baixo no código, médio para medir. Risco: médio (pode piorar relato_cafe; testar nas sondas).

**5. Uma checagem de agência nunca basta sozinha [C].**
- Evidência: postura de agência vale 1,0 < τ; AFP está **fora do catálogo** (checamos.afp.com vale 0,42); UOL por
  título vale 0,7.
- Correção:
  - R1: REFUTA de checador curado vale como selo (1,5);
  - R11: havendo REFUTA de checador, SUSTENTA de quem não é checador não vota naquela afirmação;
  - incluir AFP Checamos (e Reuters Fact Check) no catálogo curado.
- Impacto: +3 a +5 acertos (fachin, cucurella, semaforos, estribo, codigo_fonte).
- Custo: baixo. Risco: médio. codigo_fonte vira alta por uma checagem da Lupa sobre outra fala (Delgatti), um acerto de mérito duvidoso.

**6. Timeouts da SerpAPI (25 s, sem retry) derrubam buscas inteiras [C].**
- Evidência: 15 buscas em 11 casos, 355 s. É a causa primária em 4 casos B/C (china_acai, toyota, whatsapp,
  medico_robo) e secundária em bolsa e gilmar.
- Correção: timeout de 10 a 12 s com 1 retry e backoff; em replay, falha de rede segue sem cassete.
- Impacto [P]: 2 a 4 casos, e p95 −25 s.
- Custo: baixo. Risco: um pouco mais de consumo de SerpAPI (retry só em timeout).

**7. A cauda do juiz domina a latência e causou o erro A [C].**
- Evidência: oreo pendurado mais de 490 s; virginia com 441 s numa chamada; 240 s × 2 tentativas × 3 modelos;
  `JUIZ_TIMEOUT_TOTAL_S` igual ao timeout do eval; `JUIZ_CONCORRENCIA` sem uso.
- Correção:
  - timeout **total** por chamada do juiz de 45 a 60 s, com cancelamento real;
  - lotes em paralelo (2 a 3);
  - `JUIZ_TIMEOUT_TOTAL_S` menor que `--timeout-caso`;
  - `descoberta-catalogo` fora do caminho crítico (131 s).
- Impacto: +1 caso (oreo) [P]; p95 estimado de 117 s para cerca de 60 s [P].
- Custo: baixo a médio. Risco: timeout curto demais gera "sem juiz"; medir a taxa.

**8. Orçamento de crawl e de juiz gasto em páginas que nunca rendem [C].**
- Evidência: IG/FB 0/264 com corpo, PDF 0/66, listagens 75 julgadas.
- Correção:
  - não crawlear IG, FB, TikTok, X nem PDF de repositório (julgar pelo snippet, que já é o que acontece);
  - na listagem ou sidebar de agência cujo snippet contém o título da checagem, **seguir o link do artigo**
    (medico_robo, fachin, estribo).
- Impacto [P]: 2 a 3 casos; mais vagas de crawl para sites de notícia.
- Custo: baixo a médio. Risco: baixo.

**9. Falso positivo da trava VAGO [C].**
- Evidência: "Ibuprofeno piora o quadro de dengue" casa com `VAGO_RE` ("piora"); L=−2,04 foi descartado.
- Correção: VAGO só com sujeito agregado (economia, país, governo…) e sem objeto concreto; não gastar busca
  quando a entrada é vaga.
- Impacto: +1 acerto (simulado). Custo: trivial. Risco: nulo.

**10. A métrica superestima e subestima [C/P].**
- Evidência:
  - 2 acertos por acaso (garis, deus);
  - ibge com rótulo provavelmente errado;
  - 2 casos sem_evidencia→alta, que são questão de política.
- Correção: corrigir o rótulo do ibge com `nota`; decidir a política de sem_evidencia; marcar no relatório
  "acertos frágeis" (nível certo com núcleo mal extraído ou selo espúrio).
- Impacto: a leitura das iterações fica honesta. Custo: baixo. Risco: nenhum, desde que feito com justificativa.

Menores, mas reais:
- a reformulação da onda 2 com "checagem" e com entidades inventadas piora a busca [P];
- partir a comparação em 2 afirmações destrói alegações comparativas [C, 2 casos];
- o juiz sem data não percebe conteúdo "antigo" [C, jn].

---

## 4. Ranking de hipóteses (impacto ÷ custo)

| # | hipótese | casos atingidos (medido/estimado) | custo | precisa de record? | risco |
|---|---|---|---|---|---|
| 1 | **R8** selo de página só de checador | −1 grave (simulado) | muito baixo | não | nulo |
| 2 | **VAGO** mais restrito | +1 acerto (simulado) | trivial | não | nulo |
| 3 | **R2b** SUSTENTA social = 1 cluster | −1 grave, +1 parcial (simulado) | baixo | não | baixo |
| 4 | **R1+R11** + AFP no catálogo | +4 a +5 acertos (simulado, com R2b e R8) | baixo | não | médio (overfit; validar no holdout) |
| 5 | timeouts: juiz 45–60 s, paralelo, SerpAPI 10 s + retry | +1 a 4 [P]; p95 bem menor | baixo | não (código), sim p/ medir | baixo |
| 6 | trocar a busca de agências (busca interna WordPress ou `site:` único) + filtrar listagem | parte dos 24 B+C e 5 F3 [P] | médio | **sim** | baixo |
| 7 | extração mantém autor da fala e negação citada | 5–7 [P] | baixo/médio | **sim** | médio |
| 8 | não crawlear redes/PDF; seguir o link da checagem na listagem | 2–3 [P] | baixo/médio | sim (páginas novas) | baixo |
| 9 | reformulação sem "checagem", sem entidade nova (validar termos) | 1–3 [P] | baixo | sim | baixo |
| 10 | H1 (veredito pelo título de resultados de agência) | pouco hoje: quase nenhum resultado de agência do tema chega [C] | baixo | não | baixo — só rende depois do #6 |

**Pacote só de decisão, sem rede** (1 a 4 + VAGO, simulado nos 70): acerto **20→26**, acerto+parcial **37→40**,
graves **3→1** (sobra ibge, que é rótulo), indeterminadas 27→26. Os ganhos vêm de regras gerais, mas foram
desenhados olhando o dev: **rodar `--split holdout` antes de aceitar**.

O maior limite continua sendo o **recall da busca**: em 24 dos 70 casos a checagem nunca chegou ao pipeline.
Isso só se mede com record, e é onde está o próximo salto (hipóteses 6 e 7).
