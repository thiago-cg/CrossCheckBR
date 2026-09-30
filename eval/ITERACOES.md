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
