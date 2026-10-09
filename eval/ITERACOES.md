# Log de iterações

Formato de cada entrada: ver `.claude/commands/iterar.md`. Métricas do split `dev`, perfil busca aberta
(`INDICE_CHECAGENS=0`) salvo indicação.

## Iteração 0 — sistema original (2026-09-28)

Medição "antes" das correções das fases 2–4. Código: `main@ed1d168` + diff local do dono
(`modelo_fake.py`, `catalogo.json`) + só a instrumentação da fase 1 (sem mudança de lógica).

- Comando: `python3 -m eval.run --modo record --split dev --nome antes --salvar-baseline --paralelo 3 --timeout-caso 400`
- Resultado: `eval/resultados/20260928-170220-antes/` · baseline: `eval/baseline.json`
- Config: padrão (`SERP_ESTRATEGIA=agente`, `AGENTE_MAX_BUSCAS=8`), `INDICE_CHECAGENS=0` passado ao eval,
  mas **o índice ainda não tem esse gate** (a flag não teve efeito: o índice local rodou, ex. "5 checagens"
  em urnas). LLM local `unsloth/LFM2.5-8B-A1B-GGUF`; **sem `OPENROUTER_API_KEY`** → juiz sempre em
  fallback léxico; detector = mock (`FAKE_MODEL_PATH` vazio).
- Descoberta web: **rodou em 12/12** (a `SERPAPI_KEY` entrou no `.env` antes da medição). A limitação
  prevista "sem chave, descoberta pulada" não se aplicou; a limitação real desta medição é o **juiz ausente**.
- SerpAPI: 33 buscas live neste eval (+3 no smoke test da CLI antes do contador) = 36 em `eval/serpapi_uso.json`.
- Replay conferido: `python3 -m eval.run --modo replay` reproduz os 12 níveis em 0,5 s, sem rede
  (`http_miss=5` = requisições que falharam por rede/SSL no record e por isso não têm cassete; o
  resultado é o mesmo).

| métrica | valor |
|---|---|
| n | 12 |
| acerto | 0,333 (4/12) |
| acerto + parcial | 0,333 |
| **erro grave** | **0,333 (4/12)**: cafe_cura_cancer, vacina_altera_dna, urnas_fraudadas_2022, satira_feriado (falso→baixa) |
| indeterminada | 0,417 |
| níveis | baixa 7, indeterminada 5, média 0, alta 0 |
| por rótulo | falso 0/7 (4 graves, 3 indeterminada) · verdadeiro 3/4 · sem_evidencia 1/1 |
| LLM por caso | 2,0 (afirmações + padrões, locais); p50 39,9 s, p95 56,3 s por caso |
| fallback (fração de casos) | juiz 100% (10 itens/caso), openrouter.chat 100%, descoberta-site.jev 92%, padroes 58%, afirmacoes 8%, juiz.lexico-top3 8% |
| descartes | corpo curto/JS/paywall 13, fora do catálogo 9, HTTP 403 7, SSL 2 |

O que os traces mostram:

1. **Nenhum caso chega a "alta"**: todo `baixa` vem de um único sinal, "cobertura ampla" (−0,5), montado
   sobre fontes marcadas relevantes pelo fallback léxico (sem juiz). É o C1+C3 do review ao vivo: falar
   do tema reduz a propensão. Os 3 acertos em `verdadeiro` saem pelo mesmo mecanismo (acerto por acaso).
2. **A extração de afirmações corrompe a entrada**: o LLM local traduziu 2/12 para inglês ("Electronic
   voting machines were frauded…", "Government decrees…") e a busca foi feita em inglês; em
   `falso_que_vacina_dna` removeu o "É falso que" e passou a checar o próprio boato; em `CAFÉ CURA
   CÂNCER!!!` devolveu `VAZIO` (regex assumiu).
3. **Bug C5 confirmado ao vivo**: `padroes` cai no regex em 7/12 casos com
   `ValueError: not enough values to unpack` (o `return []` no ramo local quando o LLM responde NENHUM).

## Iteração 1a — núcleo de decisão + agente pós-juiz, `openai/gpt-6-luna` (2026-09-30) — **INVÁLIDA para busca**

- Config: `gpt-6-luna` (reasoning low) via OpenRouter, dev completo (70 casos: 12 manuais + 58 ClaimReview),
  `INDICE_CHECAGENS=0`, modo record, paralelo 4. Resultado: `eval/resultados/20260930-091024-iter1-luna`.
- **A cota da SerpAPI zerou no meio da rodada** (250/250; 138 buscas recusadas com HTTP 429). A maioria dos
  casos não achou fonte alguma → `indeterminada`. O harness rotulou `descoberta: rodou` em 100% (bug, corrigido:
  agora há `falhou`/`parcial`). **Não usar estes números para julgar o sistema.**

| métrica | Iteração 0 (LLM local, 12 casos) | 1a (luna, 70 casos, busca sem cota) |
|---|---|---|
| erro grave | 0,333 (4/12) | 0,029 (2/70) |
| acerto | 0,333 | 0,143 |
| indeterminada | 0,417 | 0,814 |

Único sinal aproveitável: nos casos em que a busca funcionou, `falso→baixa` deixou de ser sistemático
(vacina/urnas/Hytalo → **alta**; "É falso que a vacina altera o DNA" → baixa, correto pela polaridade).

### Trace: `cr_lula_acabar_bets_que_criou` (enganoso → **baixa**, erro grave), run `20260930-091423-66b7c7`
Com busca funcionando (24 peças, 10 julgadas). Achado:
1. **Afirmação composta partida em duas atômicas, perdendo o conector adversativo.** "Lula quer acabar com as
   bets, mas foi ele mesmo que criou elas" virou `[Lula quer acabar com as bets]` + `[Lula criou as bets]`.
   A 1ª é verdadeira (SUSTENTA, g1/Correio), e ela domina os clusters (4 com postura). O "enganoso" da checagem
   está na **relação** entre as duas, que a decomposição atômica destrói.
2. `decidir()` agrega por afirmação e soma: SUSTENTA da parte verdadeira empurra para baixa (L=-3,02).
   Hipótese: nível por afirmação e o pior caso (ou a afirmação composta original julgada inteira) no relatório.
3. Extração: 5/10 páginas sem corpo (Instagram/X/403 do UOL): esperado, não é culpa do juiz.
Ação proposta (não feita): julgar também a afirmação ORIGINAL inteira como uma "afirmação 0" e não deixar
SUSTENTA de sub-afirmação verdadeira mascarar a composta.

## Iteração 1b — mesmos 12 casos da Iteração 0, LLM local (LFM2.5-8B), busca funcionando (2026-09-30)

- Resultado: `eval/resultados/20260930-092244-iter1-local-12` (SerpAPI nova: 6 buscas live, 0 falhas; resto de cassete).
  `OPENROUTER_API_KEY=` vazio → juiz, afirmações e reformulação no modelo local.

| métrica (12 casos) | Iteração 0 | 1b |
|---|---|---|
| erro grave | 0,333 (4/12) | **0,0 (0/12)** |
| acerto | 0,333 (4/12) | 0,417 (5/12) |
| acerto + parcial | 0,333 | 0,667 (8/12) |
| indeterminada | 0,417 | 0,167 |
| níveis | 7 baixa / 5 indet. | 4 alta / 6 média / 2 indet. |
| por rótulo | falso 0/7 | falso 4/7 (3 média), **verdadeiro 0/4** (3 média, 1 indet.), sem_ev 1/1 |
| fallbacks | juiz 100%, openrouter.chat 100% | padroes 100%, extracao 83%, polaridade 42% |

- Melhoras confirmadas: nenhum falso→baixa; vacina/urnas/ibuprofeno/relato → alta.
- **Falha persistente: verdadeiro → média** (cafe_nao_cura_cancer, falso_que_vacina_dna, dengue_aedes).

### Trace `falso_que_vacina_dna` (verdadeiro → média), run `20260930-092248-d3e3ce`
A busca achou Lupa, G1 Fato ou Fake e Comprova. O erro está no **juiz + agregação**:
1. O juiz 8B local teve **4 de 10 citações rejeitadas** (inventadas): a verificação de citação funciona e evita
   ruído, mas deixa poucas fontes com postura.
2. Sobraram 2 SUSTENTA × 2 REFUTA (um deles um vídeo do YouTube com título "tem riscos no DNA?") → L=0,00 → média.
   Checagem de agência e vídeo pesam igual.
3. `0 com ClaimReview`: Lupa/G1 não publicam JSON-LD, mas o **título** já traz o veredito
   ("É fake…", "É falso que…"). Esse sinal tipado, sem LLM, é descartado nas páginas vindas da busca.

Hipóteses (a testar, uma por vez, no dev):
- H1: reaproveitar a extração de veredito por título do `ingestor` (selos.frases_de_titulo, #FAKE/#FATO) nos
  resultados da busca de fontes curadas → `veredito_pagina` sem LLM.
- H2: peso de postura maior para fonte de agência de checagem curada que para fonte não curada (hoje 'curada > não
  curada' existe, mas o efeito é pequeno frente a 2×2).
- H3: `padroes` cai em fallback em 100% (LFM não segue o formato `PADRAO:`) — baixa prioridade, fora do nível.

## Iteração 1a — `gpt-6-luna`, dev 70 casos (2026-09-30) — **INVÁLIDA** (cota SerpAPI zerada, 138 buscas 429)
Erro grave 2/70, acerto 14%, indeterminada 81% — medem a falta de busca, não o sistema. O harness marcava
`descoberta: rodou`; corrigido (`falhou`/`parcial`, aviso no relatório).

## Iteração 1b — LLM local (LFM2.5-8B), MESMOS 12 casos da Iteração 0, busca ok
Erro grave **4/12 → 0/12**; acerto 4/12 → 5/12 (8/12 com parcial); indeterminada 5/12 → 2/12; 4 casos em alta (antes 0).
Falha: verdadeiro→média (0/4). Trace `falso_que_vacina_dna`: busca achou Lupa/G1/Comprova, mas o juiz 8B local
teve 4/10 citações inventadas (rejeitadas) e sobrou 2×2 → L=0,00 → média; agência de checagem e vídeo do YouTube
pesaram igual. Afirmação composta ("X, mas foi ele que criou") é partida em atômicas e perde o conector
(`cr_lula_acabar_bets_que_criou`: enganoso→baixa).

## Iteração 2 — `gpt-6-luna` (geral) + `deepseek/deepseek-v4.1-flash` (juiz), dev 70 casos
- `OPENROUTER_MODEL_JUIZ` roteia só a finalidade `juiz`. Rodada `iter2` **inválida** de novo: teto interno
  `SERPAPI_DAILY_CAP=100` (47% dos casos sem busca). Corrigido no `.env` (1000) e refeita (`iter2b`).
- `iter2b` foi interrompida por custo antes de escrever o relatório; **os números abaixo foram reconstruídos dos
  traces** (último run de cada caso com busca ok e juiz deepseek), não de um `eval.run` completo. Os 70 casos ficaram
  válidos combinando a rodada parcial com os cassetes da anterior.

| 70 casos dev, busca ok | valor |
|---|---|
| acerto | 20/70 (29%); ≈39/70 (56%) contando `média` para falso como parcial |
| erro grave | 3/70 (4,3%): cr_jn_soltura_vorcaro, cr_chico_cesar_proibiu_direita, cr_ibge_trafico_no_pib |
| indeterminada | 27/70 (39%) |
| por rótulo | falso: 10 alta / 17 média / 19 indet. / 2 baixa · verdadeiro: 4 baixa / 2 média / 2 indet. · enganoso: 2 alta / 1 média / 5 indet. / 1 baixa |

### Causa dos 50 erros (classificação automática pelos traces)
| causa | n | leitura |
|---|---|---|
| B. só fontes fora do tema | 18 | **a busca/consulta não achou o assunto** (ex.: ibuprofeno_cura_dengue) |
| F. média com evidência unilateral fraca | 17 | 1 cluster REFUTA não basta para `alta`; é o principal "quase acerto" |
| C. só relatos sem endosso | 6 | ninguém confirma nem desmente (cafe_cura_cancer) |
| G. direção errada com evidência | 4 | erros graves reais: juiz ou polaridade |
| E. postura dividida | 3 | |
| A. nada chegou ao juiz / execução | 2 | cr_china_acai_amapa; cr_oreo_petroleo (sem nível) |

Hipóteses (NÃO implementadas; aguardam decisão):
- H1 (ataca B/C): tirar o veredito do **título** dos resultados de fontes curadas (`#FAKE`, "É falso que…") e aplicar
  como `veredito_pagina` sem LLM — reaproveita `ingestor`/`selos.frases_de_titulo`.
- H2 (ataca F): peso maior para agência de checagem curada; 1 cluster de agência refutando deveria bastar para `alta`.
- H3 (baixa prioridade): `padroes` cai em fallback com LLMs pequenos; fora do nível.
- H4 (ataca B): olhar as consultas de 5 casos B (a `consulta` do LLM pode estar perdendo a entidade central).
- H5 (ataca G): investigar os 2 falso→baixa (chico_cesar, ibge) — possível inversão de polaridade.

## Iteração 2 re-medida — Fase A (eval consertado, sistema intacto; 2026-09-30)

Branch `fase-a`. Nenhuma mudança de decisão/busca/juiz nesta fase, só harness + replay.
`pytest`: 321 passed.

- A1: `esperado_de` normalizado p/ README (falso `["alta"]`→parcial media; enganoso `["alta","media"]`→`(["alta"],["media"])`).
  Novas métricas sempre impressas: `acuracia_balanceada`, `erro_grave_por_direcao` (FE→baixa, V→alta),
  `cobertura=1-indet`, `precisao_por_nivel`, `auc_ordinal` (baixa 0, media/indet 1, alta 2; F/E vs V),
  `baselines_triviais` sempre_alta/media, Wilson 95%. Gate exige `balanceada >= sempre_alta` e sem grave novo.
- A2: `_descoberta_status` filtra `("descoberta","descoberta-agente")`; `descoberta-catalogo` ignorada.
- A3: evento `evidencias` antes de `decidir()`; `python3 -m eval.decisao --snapshot`; snapshot
  `eval/snapshots/a3-dev.jsonl` (69 linhas, oreo timeout fora): **69/69 níveis** vs `validos.json`.
- A4: lotes do juiz ordenados por URL; LLM miss em replay → `nao_reproduzido` (fora de métricas/delta).
- A4b: `LLM_ONLY_RECORD=1` congela SerpAPI/páginas no record (só LLM ao vivo) — nível 2.
- A5: `replay.cota_esgotada` interrompe o eval (pula não-iniciados, exit 2).

Nível 0 (decisão, traces antigos, 69 casos, métricas novas):
`DECISAO a3-dev`: acerto 19/69 (0,275), acerto+parcial 37/69 (0,536), grave 3/69, indet 27/69.

Nível 1 (record busca congelada + LLM vivo p/ nova ordem do juiz, dev 70):
`20260930-154821-fase-a-llm-frozen`: acerto 16/70 (0,229), parcial 34/70 (0,486), **grave 1/70**
(FE→baixa 1/58, V→alta 0/8), indet 31/70, balanceada 0,346 (sempre_alta 0,50, sempre_media 0,19),
AUC 0,786, cobertura 0,557. Precisão: alta 12 casos, baixa 5. SerpAPI live 0 neste eval
(total 440→450 por 10 buscas de um record parcial sem freeze, antes do A4b; resto reuse).
`llm_miss=0`, `http_miss=124` (páginas 403/SSL/PDF que também falharam no record).
Replay `20260930-162345-fase-a-replay2`: n=69 + 1 `nao_reproduzido` (llm_miss 3),
acerto 16/69, grave 1, balanceada 0,347, AUC 0,785 — **±1 caso vs record**.

Leitura honesta: com métricas novas o sistema continua abaixo do trivial em balanceada
(0,35 < 0,50) e o único sinal segue em `baixa`/AUC. O `sempre_alta` tem 8 graves (8/8 V→alta);
o sistema tem 1 grave real restante + 0 em V. Diferença snapshot (3 graves) vs novo (1 grave)
vem do juiz live re-gravado (não-determinismo) + 10 queries novas do record parcial — não de regra.
Sem validação: replay 70/70 idêntico duas vezes (aborts nativos flaky SIGABRT/SIGSEGV em
trafilatura/PDF impediram a 3ª perna; 2 pernas OK ±1 caso) e holdout (nível 4, ~25 casos record:
pedir aprovação antes).

## Iteração E1 — base primeiro, web condicional (Task 3: medição nível 0; 2026-10-07)

Branch `feat/e1-base-primeiro`. Docs/metrificação, sem mudança de código de produto nesta task.

- Hipótese: quando a base (índice de checagens) já contém checagem **aplicável** ao fato
  (selo com direção, citação verificada, corpo lido, mesmo fato, data compatível), pular a
  descoberta web economiza buscas (custo SerpAPI + latência) sem perder acerto — o veredito
  da base já decide.
- Mudança (Tasks 1–2, commits `b40bf25` + `810a4f3`): Task 1 = gate `e_aplicavel(...) ->
  tuple[bool, str]` + `data_compativel(...)` (`factcheck_mvp/aplicabilidade.py`); Task 2 =
  pipeline base→leitura→juiz→aplicabilidade com web condicional + recibo `descoberta/pulada`
  (`"web pulada: checagem aplicável na base"`). Fora de escopo de propósito: E2
  (`onde_encontrado`), E3 (título não vota), E5 (selo atribuído), E4-elevação de propensão.
- **eval.run completo não roda aqui (sem cassetes)**: só medição offline (nível 0 + bench).
  Sem rede, sem custo SerpAPI.
- Nível 0 (decisão, snapshot antigo `eval/snapshots/a3-dev.jsonl`, 69 casos — traces de
  2026-09-30, anteriores ao E1, logo o E1 **não move** este número por construção; o ganho
  esperado é custo/web pulada, só mensurável em `eval.run` futuro):

| métrica | referência | E1 (esta medição) | delta |
|---|---|---|---|
| acerto | 24,6% | 0,2464 (17/69) | 0 |
| acerto+parcial | 56,5% | 0,5652 (39/69) | 0 |
| erro_grave | 1 | 1 (`cr_jn_soltura_vorcaro`, FE→baixa) | 0 |
| indeterminada | 39% | 0,3913 (27/69) | 0 |

- Bench índice (`python3 scripts/bench_indice_checagens.py`): `recall@3 0,8636 (~0,86)`,
  `FP 0,0789 (~7,9%)` — sem regressão vs referência.
- Testes: suite completa `368 passed` (inclui os 5 novos do E1 — Task 1:
  `test_aplicavel_exige_corpo_citacao_selo_data`, `test_data_incompativel_barra_bolsonaro`;
  Task 2: `test_base_aplicavel_pula_web`, `test_base_inaplicavel_chama_web`,
  `test_checagem_antiga_para_fato_de_hoje_nao_pula_web`). Sem run_ids novos de trace:
  nenhum `eval.run`/CLI `checar` foi executado nesta task (sem cassetes); os run_ids no
  snapshot (`20260930-...`) são dos traces antigos reutilizados.

## Iteração Fase E/B follow-up + E4 (09/10 — 2026-10-09)

Branch `feat/fase-eb-followup` @ `c7a8868` (base `main` @ `56c4b33`). Sem rede, sem cassetes e sem `runs/` neste ambiente: só medição de nível 0 (offline) e pytest. Nenhum `eval.run` foi executado.

- Hipótese:
  - **T6 (B1c):** página que o BERTimbau acha fake quase não deve votar, e selo ClaimReview de página não deve decidir o nível (só o selo do índice vota). Previsão: `cr_jn_soltura_vorcaro` (selo do template do SBT, erro grave) sai de baixa.
  - **T7 (B2):** o máximo entre afirmações erra nas duas direções. Com conjunção (`p_texto = 1 − Π(1 − σ(L_a))`), A (L=+2) + B (L=−5) deve dar alta, e a parte falsa abaixo de τ não deve deixar a verdadeira decidir. Previsão: `cr_lula_acabar_bets_que_criou` sai de baixa.
  - **E4:** fonte publicada fora da janela do texto ("hoje", "ontem", "nesta semana") perde peso, sem zerar e sem elevar a propensão. Fonte de outro episódio não deve decidir fato apresentado como atual. Previsão: nenhum efeito no a3-dev (snapshot sem datas).
- Evidência (run_ids / eventos): nenhum run_id novo. Run_ids olhados: nenhum (sem `runs/` neste ambiente). Evidência = `eval/snapshots/a3-dev.jsonl` (69 casos) reexecutado com `eval.decisao` no HEAD e no `main` (56c4b33, exportado para uma pasta temporária).
- Mudança:
  - T6 (`6a4533c`): juiz combina avaliador e BERTimbau por página; peso da postura = `W_POSTURA × f_fonte × (1 − prob_fake_pagina)`; selo ClaimReview de página não vota. Sem o BERTimbau carregado o fator é 1,0 e o mock não entra na decisão.
  - T7 (`f0e0959`, fix `7b955ca`): combinação por conjunção entre afirmações; afirmação só com evidência descontada (|L| < τ) fica fora da conjunção.
  - E4 (plano `docs/superpowers/plans/2026-10-09-e4-relevancia-temporal.md`): `r` hiperbólica (Task 0); direção e bits medidos antes do desconto, com contrafactual `nivel_sem_desconto` (Task 1); `normalizar_data` e prioridade da fonte da data (Task 2: `b13087d`, `19170a5`, `4adac8f`); relógio gravado via `replay.hoje` (Task 3: `9436f01`, `7c691ee`); marcadores com exclusões e janela por afirmação (Task 4: `50bf6a7`, `15a444d`); marco da data do fato, com data explícita vencendo o marcador relativo (Task 4b: `0203bbb`); gate E1 com a mesma janela, referência e data (Task 5: `4b575ec`); aviso neutro no bot, na API e na web (Task 6: `762502e`, `2b5bbf4`); telemetria, `--sem-e4` e `--gerar-snapshot` (Task 7: `6761257`, `644c825`, `f494749`); trava `so_fontes_de_outro_periodo` (`a863228`).
  - Raciocínio do avaliador (até ~240 caracteres, sem corte no código) no bot e na web: `17f385b`.
  - Correções de review: `c86b6f8` (avaliador julga cada par uma vez, com cache por afirmação + URL canônica), `b74c4a4` (contagem de lidas inclui a fase base), `e868fd2` (contagem de fontes só das afirmações que pesaram), `b08a37a` (entrada por link usa a data da própria página; sem data o E4 fica desligado), `fbeba17` (sem exceções silenciosas nos descontos).
- Métricas dev (antes → depois), nível 0: `python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl`, n=69, sem rede:

| métrica | antes (main `56c4b33`) | só T6 | só T7 | final (HEAD `c7a8868`) |
|---|---|---|---|---|
| acerto | 0,1594 (11/69) | 0,1449 (10/69) | 0,1594 (11/69) | 0,1449 (10/69) |
| acerto + parcial | 0,3043 (21/69) | 0,3188 (22/69) | 0,3188 (22/69) | 0,3333 (23/69) |
| **erro grave** | **2** | **1** | **1** | **0** |
| indeterminada | 0,6522 (45/69) | 0,6522 (45/69) | 0,6522 (45/69) | 0,6522 (45/69) |
| níveis | indet 45 · média 14 · alta 6 · baixa 4 | indet 45 · média 16 · alta 5 · baixa 3 | indet 45 · média 15 · alta 6 · baixa 3 (derivado) | indet 45 · média 17 · alta 5 · baixa 2 |

  - "Só T6" e "só T7" são ablações medidas nesta rodada, não reexecutadas no HEAD. "Antes" e "final" foram reexecutados e batem com a saída do comando.
  - Casos que mudaram entre `main` e HEAD (só estes três; verificado caso a caso):
    - `cr_jn_soltura_vorcaro` (enganoso): baixa (L=−1,60, **grave**) → média (L=−0,40). T6: o selo ClaimReview veio do template do SBT e deixa de votar.
    - `cr_lula_acabar_bets_que_criou` (enganoso): baixa (L=−2,60, **grave**) → média (L=+0,709). T7: as partes "Lula quer acabar com as bets" (L=−2,60) e "Lula criou as bets" (L=+0,60) entram na conjunção e o resultado fica em média.
    - `cr_lula_nao_acredita_em_deus` (falso): alta (L=+1,80, **acerto**) → média (L=+0,60, parcial). T6: mesmo mecanismo do selo de página. Trade-off aceito pela usuária (ver decisões).
  - `eval.decisao` também confere contra `docs/review/review2/sondas/validos.json` (traces de 30/09) e imprime "reproduz N/69": `main` 42/69, HEAD 40/69. As duas divergências novas são vorcaro e nao_acredita (o `validos.json` tem baixa e alta). O restante já estava em `main`, sobretudo pelo E3 (indeterminada 27/69 no E1, 45/69 agora). O `validos.json` não é gabarito; o docstring de `eval/decisao.py` ("confere 69/69") está desatualizado.
- Modo: nível 0 offline · buscas SerpAPI live: 0.
- Decisão: mantida (aceita pela usuária em 09/10) · baseline atualizado? não (`eval/baseline.json` só muda depois do record).
- Regressão Bolsonaro (base real do índice, offline, `r` stub = 0,05; teste em `0ebcd57`): REFUTA → sem E4 alta (L=+1,50), com E4 média (L=+0,16); SUSTENTA → sem E4 baixa (L=−3,00), com E4 média (L=−0,25).

### Decisões da usuária (09/10)

1. E4: aceito que o nível fique mais extremo quando a fonte antiga de sinal oposto perde peso, desde que a direção venha de fontes dentro do período. Extremo sustentado só por fonte descontada: não (trava `so_fontes_de_outro_periodo`).
2. Task 4b: endurecer e ligar.
3. Data explícita na afirmação vence o marcador relativo ("disse hoje que a ponte caiu em 2019").
4. T6: aceito o trade-off `cr_jn_soltura_vorcaro` (grave corrigido) × `cr_lula_nao_acredita_em_deus` (acerto → parcial).
5. T7: afirmação só com evidência descontada (|L| < τ) fica fora da conjunção; votos atuais que se anulam entram com 0,5; soma de partes fracas → alta aceita, acompanhar no record.

### Não validado

- Record da subamostra (~60 buscas SerpAPI) e replay: pendentes de aprovação da usuária e de cassetes (não existem nesta máquina). Os efeitos de T6 e T7 são só offline, sem busca real.
- Holdout: pendente (aprovação e cassetes; ~60 buscas).
- Cobertura de datas (Task 2, Step 5): não medida (sem `runs/`).
- E4 não é medido no nível 0: o a3-dev não tem datas (`itens_com_data_pub` = 0; `e4.referencia_ausente` = 69). Só 4 casos dev têm 2 ou mais afirmações (`cr_bolsa_familia_15_aposentadoria_39`, `cr_codigo_fonte_urnas_moraes`, `cr_lula_acabar_bets_que_criou`, `cr_vorcaro_pagou_dino`), e são os únicos em que T7 pode mexer.
- Marcadores temporais: 2 casos dev (`cr_hytalo_santos_morreu`, `cr_el_nino_catastrofe_setembro`) e 1 holdout (`cr_video_lula_ministros_stf_atual`). Amostra pequena.
- Curva `r` hiperbólica não calibrada (escolha da usuária; calibrar com pares rotulados fica para depois).
- Suíte: 688 passed, 0 failed, 0 xfail, com e sem `PYTHONUTF8=1` (`main`: 467 passed com `PYTHONUTF8=1`; 3 falhas de encoding sem ele).

### Holdout (dev × holdout)

| split | medição | acerto | acerto + parcial | erro grave | indeterminada |
|---|---|---|---|---|---|
| dev | nível 0 (a3-dev, final) | 0,1449 | 0,3333 | 0 | 0,6522 |
| dev | record/replay da subamostra | pendente (aprovação + cassetes) | | | |
| holdout | nível 4 | pendente (aprovação + cassetes) | | | |

### Adendo: code review final (09/10)

Base: `feat/fase-eb-followup` @ `62c6156`, 39 commits depois de `0c9267c` (o commit que registrou esta entrada). Entre `c7a8868` e `0c9267c` só mudaram docs, então o código medido aqui é o mesmo da entrada. Sem rede, sem record, sem holdout e sem `eval.run`. `eval/cassettes/`, `runs/` e `eval/resultados/` não existem nesta máquina.

**Decisões da usuária (pós-review, 09/10)**

1. **Conjunção (T7):** só partes com L_a ≥ 0 (contestadas, e divididas com L_a = 0) entram em `p = 1 − Π(1 − σ(L_a))`. Se nenhuma tem L_a ≥ 0, L = max(L_a). Motivo: pela fórmula antiga, 5 partes com 1 SUSTENTA curada cada davam alta (L = +1,33). Agora dão média (L = −1,00).
2. **Parte só de outro período:** afirmação que só tem fonte fora da janela sai da conjunção. Se por isso o nível sairia baixa, o texto fica em média (trava `parte_sem_checagem_atual`, L = −0,99τ). A alta pelas partes contestadas atuais se mantém.
3. **Postura quase toda descontada pelo BERT:** postura cujo fator (1 − prob_fake) fica abaixo de 5% do peso sem o modelo não vota (`FRACAO_MIN_VOTO = 0,05`, ou seja prob_fake > 0,95). Vai para `posturas_fracas` no trace.

**Correções depois da entrada**

- Trava `so_fontes_de_outro_periodo`: nunca extremo sustentado só por fonte descontada. Não tira afirmação que tem voto atual (C1).
- `sem_fonte_confiavel` só conta fonte atual, isto é, dentro da janela.
- Paridade gate × decisão: o gate E1 e `decidir` recebem o mesmo texto, a mesma referência e a mesma data normalizada, e medem com `marcas_da_afirmacao` (janela e marco) e `medida_da_afirmacao` (marco antes da janela). `AfirmacaoDecisao.calculada` marca que a medida veio do pipeline (pipeline.py:591 e 639-649).
- Gate E1 não aceita selo de página (T6, `_selo_vota`). Consequência: selo de página também não encerra a busca do agente (pipeline.py:955). Em live, a busca pode gastar mais SerpAPI.
- Task 4b endurecida: intervalos no início, frações ("8/10"), datas recorrentes ("todo dia 5"), comemorativas, verbo no futuro, D > referência e 2+ datas distintas não ancoram o marco.
- Data explícita vence o marcador relativo: o marco vem antes da janela.
- Normalização de datas: meia-noite local, formatos PT/EN/RFC, relativas medidas no fim do intervalo.
- Relógio sem default escondido: sem `data_referencia` na entrada e sem relógio gravado, o E4 fica desligado (pipeline.py:305-311). Nunca usa o dia de hoje em silêncio.
- Neutralidade: raciocínio com expressão proibida é omitido (`linha_raciocinio`, fallback `raciocinio`). A varredura normaliza NFKC e espaços, tira caracteres de formatação e compara também sem acento.
- Web só cria link `http(s)`; outro esquema vira texto escapado (`api._link_html`).
- Bot mede a mensagem em UTF-16, como o Telegram (`_len_telegram`).
- Link usa a data da própria página; sem ela, a do encaminhamento; sem as duas, o E4 fica desligado (`entrada_de_link`, `ref_fallback`).
- Os bugs "Lidas 0" e avaliador julgando cada par 2× já estavam corrigidos antes desta entrada (já registrados acima: `b74c4a4`, `c86b6f8`).

**Números confirmados (HEAD `62c6156`)**

- pytest: **889 passed, 0 failed**, com e sem `PYTHONUTF8=1` (`-p no:cacheprovider`).
- `python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl` (n=69): acerto 0,1449 · acerto+parcial 0,3333 · erro grave 0 · indeterminada 0,6522 · níveis {indeterminada 45, média 17, alta 5, baixa 2}. Reproduz 40/69 vs `validos.json`.
- Caso a caso contra `0c9267c` (mesmo snapshot, mesmos 69 casos): só `cr_lula_acabar_bets_que_criou` muda, média L=+0,7089 → média L=+0,6000. Nenhum nível muda e nenhum erro grave aparece.
- Cenários conferidos com `decidir` (fora do repo): REFUTA curada com prob_fake 0,94 → média (L=+0,06); com 0,96 → baixa (L=−2,00); 5 SUSTENTA curadas → média (L=−1,00); duas partes divididas → L=τ → alta.

**Pendências (não validadas ou follow-ups)**

- Record da subamostra (~60 buscas SerpAPI) e holdout (~60): aguardam aprovação e cassetes.
- Descontinuidade D-a + D-c: REFUTA curada com prob_fake 0,94 dá média (+0,06); com 0,96 a postura some (fração abaixo de 5%) e as confirmações decidem (baixa). Conhecida, sem decisão.
- Duas partes divididas (L_a = 0 cada) dão L = τ e alta. Documentado em teste; mantido pela decisão de 09/10.
- Motivo impreciso: com a única postura descartada pelo BERT, `decidir` diz "as fontes com postura se anulam…" (ramo final do motivo).
- Artigo do selo ("da"/"do") por heurística do primeiro nome (`_artigo_da_agencia`). A neutralidade não depende dele.
- `decisao._citar_afirmacao` checa expressão proibida por substring crua em `texto.lower()`, sem normalizar acento: texto do usuário sem acento pode escapar da checagem.
- `/checar --json` devolve o raciocínio bruto do avaliador. `linha_raciocinio` só age no bot e na web.
- Reforçar o prompt do juiz para não usar "é falso" no raciocínio. Muda o prompt e invalida os cassetes LLM: fazer junto com o próximo record.
- `pipeline` chama `motivo_data_ilegivel` sem âncora em 3 pontos (pipeline.py:278, 374, 737): uma relativa com âncora válida sai com o motivo "sem âncora".
- `pipeline.py:22` importa `datetime` e `timezone` sem uso.
- O docstring de `eval/decisao.py` diz "confere 69/69"; o número real é 40/69 (`validos.json`).

### Adendo: pendências menores fechadas (09/10, itens 1-5)

- `_citar_afirmacao` usa `verificar_neutralidade` (normaliza acento): "E falso que X" vira "a afirmação N".
- `FRACAO_MIN_VOTO` (5%) vale também para o desconto temporal (E4): contribuição descontada abaixo de 5% do valor bruto não vota. A afirmação que fica sem voto por isso continua contando como "parte sem checagem atual" (D-b). Efeito colateral: com r = 0 (antes: sem voto e fora de D-b) o texto agora também limita baixa a média.
- Motivo próprio e neutro quando a única postura é descartada pelo BERT.
- `motivo_data_ilegivel` recebe a âncora da busca em `_peca_web` (`_ancora`). Em `pipeline.py` (índice, página lida) `normalizar_data` roda sem âncora, então "sem âncora" é o motivo correto ali.
- Import sem uso removido de `pipeline.py`; docstring de `eval/decisao.py` corrigido.
- Medição: pytest 894 passed; `eval.decisao --snapshot eval/snapshots/a3-dev.jsonl`: acerto 0,1449, acerto+parcial 0,3333, erro grave 0 (igual ao anterior).
- Item 6 (`/checar --json` devolve raciocínio bruto): aguarda decisão da usuária.
