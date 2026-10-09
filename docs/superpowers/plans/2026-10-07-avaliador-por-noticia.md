# Avaliador-por-notícia Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Todo resultado relevante do Serp/base passa por deep crawl e por 1 chamada do LLM-avaliador com manchete+corpo; o juiz final decide sobre os outputs do avaliador.

**Architecture:** Inverter a ordem atual (selecionar-por-metadados → crawlear poucos) para (crawlear todos os relevantes → avaliar 1:1 com corpo → agregar). Criar `factcheck_mvp/avaliador.py` como o estágio 1:1; `juiz_llm.py` vira motor interno do avaliador (1 item por chamada); `decisao.decidir` continua sendo o juiz-agregador determinístico sobre `ItemEvidencia`.

**Tech Stack:** Python 3, `trafilatura/bs4` via `extracao.py`, `aprofundar.py` (deep crawl), `llm.chat_json` + Pydantic (`avaliador.py`), `decisao.py` (log-odds), `pytest`.

**Spec:** Conversa do dono em 07/10/2026 — "a análise apenas dos metadados pelo llm_avaliador nunca deveria acontecer, ele deveria receber o corpo + manchete e retornar para o juiz url, titulo, snippet, fonte.nome, data_publicacao, posicao (posicao = se corrobora a afirmação); chamar 1 llm_avaliador para cada resultado relevante do Serp ou da base (sempre entrando no site e puxando manchete e corpo); o juiz recebe os outputs e dá o veredito" + refinamento "com deepcrawl e o llm avaliador julgando cada noticia considerando titulo e corpo" + decisão "remover o cap diário de LLM (`LLM_DAILY_CAP`) em todo o código — sem limite diário de chamadas" (o N×1 do avaliador multiplica as chamadas e o cap de 50/dia travaria o fluxo a partir da ~3ª consulta).

## Global Constraints

- `python3` (não existe `python`); `python3 -m pytest tests -q` sempre verde antes e depois.
- Todo fallback novo emite `telemetria.fallback(onde, motivo)`; todo I/O novo usa `replay.*` (determinismo record/replay).
- Nunca editar casos, rótulos, `esperado` ou baseline para passar; iterar só no split `dev`.
- Não ajustar pesos/limiares para acertar um caso específico; mudança com razão geral.
- `JUIZ_CITACAO_MIN=0.9`, `JUIZ_TRECHO_MAX=2500`, `TAU=ln 3`, `W_POSTURA=1.0`, `W_VEREDITO=1.5`, `F_SO_TITULO=0.7` inalterados salvo tarefa explícita.
- `url/titulo/fonte.nome/data_pub` são determinísticos (Serp/crawl/catálogo) — o LLM nunca os gera, só `posicao/citacao/pagina_diz`.
- Sem `LLM_DAILY_CAP`: nenhuma chamada OpenRouter (`chat`, `decisions`) ou local é bloqueada por cota diária; `SERPAPI_DAILY_CAP=100` (busca) é mantido.

## Review Focus

- Página com paywall/JS/corpo < 500 chars: avaliador deve marcar não-julgada com `corpo_lido=False`, nunca `SUSTENTA/REFUTA` de snippet.
- Republicação do mesmo texto em 3 domínios: deve virar 1 voto (cluster), não 3 posturas.
- Citação copiada que não existe no trecho: rebaixar para `NAO_TRATA` com `citacao_verificada=False`.
- 25 resultados relevantes × 3 afirmações: latência e `SERPAPI_DAILY_CAP=100` não podem travar a consulta (orçamento parcial preservado).
- ClaimReview da página sobre outra alegação: `veredito_pagina` ignorado salvo mesma afirmação.
- Sem cap diário (Task 0): custo OpenRouter fica ilimitado (N avaliações por consulta) — espera-se controle por fatura/monitoramento, não por bloqueio em código; erros reais continuam com `fallback openrouter.chat` e fallback para o modelo local.

---

### Task 0: Remover o cap diário de LLM (pré-requisito do N×1)

**Files:**
- Modify: `factcheck_mvp/llm_openrouter.py` (remover `_uso_dia`, `_dia_atual`, `_hoje()`, `_cap_estourado()`, checagens em `chat()` e `decisions()`, incrementos de uso; limpar imports `defaultdict`/`date`; ajustar docstrings)
- Modify: `factcheck_mvp/config.py` (remover `LLM_DAILY_CAP = _int("LLM_DAILY_CAP", 50)`)
- Modify: `factcheck_mvp/pipeline.py` (`executar`: remover `"LLM_DAILY_CAP": config.LLM_DAILY_CAP` do dict de telemetria do run)
- Modify: `tests/test_loop1.py` (remover os 2 `monkeypatch.setattr(oo, "_cap_estourado", lambda: False)`), `tests/test_llm.py` (trocar `RuntimeError("teto LLM diário atingido")` simulado por `RuntimeError("openrouter falhou")`)
- Test: `tests/test_loop1.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: `llm_openrouter.chat(messages, max_tokens, timeout_s, modelos) -> Tuple[str, str]`; `llm_openrouter.decisions(model, state, questions, timeout_s) -> Dict`
- Produces: `chat`/`decisions` sem nenhuma checagem de cota (chamadas ilimitadas; sem `RuntimeError("teto LLM diário atingido")`, sem `telemetria.fallback("openrouter.chat", "teto ...")`); `config` sem atributo `LLM_DAILY_CAP`

- [ ] **Step 1: Write the failing test** — `test_sem_cap_diario_llm` em `tests/test_loop1.py`: setar `OPENROUTER_API_KEY="k"`, mockar `_post` para sempre retornar texto válido, chamar `oo.chat(...)` 60× em sequência; assert que as 60 passam e que `not hasattr(oo, "_cap_estourado")` e `not hasattr(config, "LLM_DAILY_CAP")`.
- [ ] **Step 2: Run test to verify it fails** — Run: `python3 -m pytest tests/test_loop1.py::test_sem_cap_diario_llm -v` — Expected: FAIL (hoje a 51ª chamada levanta `RuntimeError("teto LLM diário atingido")` com o default 50).
- [ ] **Step 3: Implement a remoção em `llm_openrouter.py`, `config.py`, `pipeline.py`** — deletar `_uso_dia/_dia_atual/_hoje/_cap_estourado`, os dois `if _cap_estourado()` (em `chat` e `decisions`), os incrementos `_uso_dia[...] += 1` e a chave `LLM_DAILY_CAP` da telemetria; limpar os monkeypatches de `_cap_estourado` nos testes.
- [ ] **Step 4: Run test to verify it passes** — Run: `python3 -m pytest tests/test_loop1.py tests/test_llm.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add factcheck_mvp/llm_openrouter.py factcheck_mvp/config.py factcheck_mvp/pipeline.py tests/test_loop1.py tests/test_llm.py && git commit -m "feat: remove cap diario de LLM"`

### Task 1: Crawl-primeiro (inverter seleção × leitura)

**Files:**
- Modify: `factcheck_mvp/pipeline.py` (`_selecionar`, `_ler`, `_executar`)
- Modify: `factcheck_mvp/config.py` (`DEEP_CRAWL_TOTAL`, `DEEP_CRAWL_MAX_PAGES`)
- Test: `tests/test_pipeline.py` (adicionar casos; não alterar os existentes)

**Interfaces:**
- Consumes: `aprofundar.aprofundar(cands, catalogo, por_afirm, budget_total, max_bytes) -> Dict[str, CorpoLido]`; `CorpoLido.corpo_lido: bool`, `.texto_completo`, `.trecho_corpo`, `.metodo`, `.titulo`, `.data_pub`, `.veredito_pagina`
- Produces: `pecas` com `corpo/trecho_juiz/metodo/titulo/data_pub/veredito_pagina` preenchidos para TODAS as peças relevantes antes de qualquer julgamento; `selecionados` = só peças com `corpo_lido=True` (ou snippet marcado `corpo_lido=False`)

- [ ] **Step 1: Write the failing test** — `test_crawl_antes_do_filtro_lexical` em `tests/test_pipeline.py`: 3 peças (uma com `overlap` baixo mas corpo com termos da afirmação), mock de `aprofundar` devolvendo corpo lido para as 3; assert que as 3 chegam ao estágio de julgamento.
- [ ] **Step 2: Run test to verify it fails** — Run: `python3 -m pytest tests/test_pipeline.py::test_crawl_antes_do_filtro_lexical -v` — Expected: FAIL (hoje só as de overlap alto são lidas/julgadas, teto `JUIZ_MAX_NOTICIAS=10` corta antes do crawl).
- [ ] **Step 3: Implement em `pipeline.py`** — mover a chamada `aprofundar` para antes do `_selecionar` final: crawlear `min(len(pecas), DEEP_CRAWL_TOTAL realinhado)` peças por consulta; `_selecionar` passa a ordenar mas não mais a excluir do crawl; peças com `corpo_lido=False` seguem adiante marcadas (não excluídas aqui).
- [ ] **Step 4: Run test to verify it passes** — Run: `python3 -m pytest tests/test_pipeline.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add factcheck_mvp/pipeline.py factcheck_mvp/config.py tests/test_pipeline.py && git commit -m "feat: crawl-primeiro antes do filtro lexical"`

### Task 2: Criar `avaliador.py` 1:1 (manchete+corpo obrigatórios)

**Files:**
- Create: `factcheck_mvp/avaliador.py`
- Test: `tests/test_avaliador.py`

**Interfaces:**
- Consumes: `juiz_llm.julgar_lote(afirmacao: str, itens: List[Dict]) -> List[Dict]` com 1 item; `juiz_llm.montar_trecho(titulo, corpo, afirmacao, limite) -> str`; `juiz_llm.verificar_citacao(citacao, texto) -> float`
- Produces: `avaliar(afirmacao_nucleo: str, peca: Dict[str, Any]) -> Dict[str, Any]` com `{"posicao": "SUSTENTA|REFUTA|RELATA_SEM_ENDOSSO|NAO_TRATA|None", "citacao": str, "citacao_score": float|None, "citacao_verificada": bool|None, "pagina_diz": str, "motor": str, "erro": str|None, "corpo_lido": bool}`. `peca` de entrada: `{url, titulo, corpo|texto_completo|trecho_juiz, snippet, veiculo, dominio, data_pub, veredito_pagina, corpo_lido}`. Regra dura: sem `titulo+corpo` (só snippet) → `posicao=None`, `erro="sem corpo lido"`, `motor="fallback-sem-corpo"`.

- [ ] **Step 1: Write the failing test** — `test_avaliador_exige_corpo_e_retorna_posicao` em `tests/test_avaliador.py`: (a) peça com titulo+corpo que sustenta → `posicao=="SUSTENTA"`, `citacao_verificada is True`; (b) peça só com snippet → `posicao is None`, `motor=="fallback-sem-corpo"`. Mockar `llm.chat_json` para (a).
- [ ] **Step 2: Run test to verify it fails** — Run: `python3 -m pytest tests/test_avaliador.py -v` — Expected: FAIL with "No module named factcheck_mvp.avaliador" (ou `avaliar not defined`).
- [ ] **Step 3: Implement `avaliar()` em `factcheck_mvp/avaliador.py`** — montar 1 item `{veiculo, dominio, tipo_fonte, data, titulo, veredito_pagina, trecho=juiz_llm.montar_trecho(titulo, corpo, nucleo)}`; chamar `juiz_llm.julgar_lote(nucleo, [item])[0]`; remapear `classe→posicao`; sem corpo (`corpo_lido=False` e texto < 500) retornar fallback sem chamar LLM. `url/titulo/snippet/fonte/data` nunca saem do LLM.
- [ ] **Step 4: Run test to verify it passes** — Run: `python3 -m pytest tests/test_avaliador.py tests/test_juiz_llm.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add factcheck_mvp/avaliador.py tests/test_avaliador.py && git commit -m "feat: avaliador 1:1 manchete+corpo"`

### Task 3: Pipeline usa o avaliador 1× por (peça, afirmação)

**Files:**
- Modify: `factcheck_mvp/pipeline.py` (`_julgar`, `_ondas_extras`)
- Modify: `tests/test_pipeline.py`
- Test: `tests/test_pipeline.py::test_julga_cada_peca_com_corpo`

**Interfaces:**
- Consumes: `avaliador.avaliar(afirmacao_nucleo, peca) -> Dict` (Task 2); `corroboracao.agrupar(pecas)` para clusters antes/depois (clusters não mudam o nº de chamadas, só os votos)
- Produces: `julg: Dict[(pi, ai), {"classe": posicao, "citacao": ..., "citacao_verificada": ..., "motor": ..., "erro": ...}]` com 1 chamada de avaliador por par julgado; `_overlap` mantido só como ordenação, nunca como gate de exclusão do crawl/avaliação

- [ ] **Step 1: Write the failing test** — `test_julga_cada_peca_com_corpo`: pipeline com 4 peças com corpo lido (mock `aprofundar` + mock `avaliador.avaliar` contando chamadas); assert `avaliar` chamado 4× e cada chamada recebeu `titulo` e `corpo` não-vazios.
- [ ] **Step 2: Run test to verify it fails** — Run: `python3 -m pytest tests/test_pipeline.py::test_julga_cada_peca_com_corpo -v` — Expected: FAIL (hoje `_julgar` chama `juiz_llm.julgar` em lote direto, sem `avaliador`, e só para `selecionados`).
- [ ] **Step 3: Implement em `pipeline.py::_julgar`** — substituir `juiz_llm.julgar(afirmacao, itens)` por loop `avaliador.avaliar(afs[ai].alvo(), peca_com_corpo)` por par `(pi, ai)`; manter contratos de `julg`, telemetria `fonte/juiz` e `rebaixado` por citação; ondas extras reutilizam o mesmo caminho.
- [ ] **Step 4: Run test to verify it passes** — Run: `python3 -m pytest tests/test_pipeline.py tests/test_avaliador.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add factcheck_mvp/pipeline.py tests/test_pipeline.py && git commit -m "feat: pipeline avalia 1:1 por peca com corpo"`

### Task 4: Juiz final sobre outputs do avaliador (sem LLM novo)

**Files:**
- Modify: `factcheck_mvp/pipeline.py` (`_fontes`, montagem de `ItemEvidencia`)
- Modify: `factcheck_mvp/decisao.py` — só documentação do papel de juiz-agregador (sem mudar fórmula/pesos)
- Test: `tests/test_decisao.py` (adicionar caso fim-a-fim de agregação; não retocar pesos)

**Interfaces:**
- Consumes: `julg[(pi, ai)]` do avaliador (Task 3) + `pecas[i].cluster` de `corroboracao.agrupar`
- Produces: `decisao.Evidencias(itens=[ItemEvidencia(url, afirmacao, cluster, classe=posicao, motor, citacao_verificada, curada, corpo_lido, veredito, origem_veredito, veiculo, confiabilidade)])` → `decidir(ev) -> Decisao(nivel, log_odds, votos)`; `FonteEvidencia` final com `postura=posicao`, `relevante=posicao in TRATA`, `corpo_lido=True`

- [ ] **Step 1: Write the failing test** — `test_juiz_agrega_avaliacoes`: 2 clusters `REFUTA` curados com `citacao_verificada=True` → `nivel=="alta"`; 0 posturas (só `RELATA/NAO_TRATA`) → `nivel=="indeterminada"` com motivo "nenhuma fonte confirma ou contesta".
- [ ] **Step 2: Run test to verify it fails** — Run: `python3 -m pytest tests/test_decisao.py -q` — Expected: FAIL apenas no caso novo (fórmula existe, mas o wiring `avaliador→ItemEvidencia(corpo_lido=True)` ainda não está ligado no pipeline).
- [ ] **Step 3: Implement o wiring em `pipeline.py`** — `ItemEvidencia.classe = julg.posicao`, `corpo_lido = peca.corpo_lido`, `citacao_verificada` do avaliador; `F_SO_TITULO=0.7` aplicado quando `corpo_lido=False`; atualizar docstring de `decisao.py` ("juiz-agregador sobre outputs do avaliador").
- [ ] **Step 4: Run test to verify it passes** — Run: `python3 -m pytest tests/test_decisao.py tests/test_pipeline.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git add factcheck_mvp/pipeline.py factcheck_mvp/decisao.py tests/test_decisao.py && git commit -m "feat: juiz final agrega posicoes do avaliador"`

### Task 5: Orçamento, timeouts e telemetria do N×1

**Files:**
- Modify: `factcheck_mvp/config.py` (`DEEP_CRAWL_TOTAL`, `DEEP_CRAWL_TIMEOUT_S`, `JUIZ_TIMEOUT_TOTAL_S`, comentário de `JUIZ_MAX_NOTICIAS` como teto de exibição, não de avaliação)
- Modify: `factcheck_mvp/pipeline.py` (etapas `deep-crawl`, `avaliador`, `juiz`; `telemetria.evento("fonte", estagio="avaliador", ...)`)
- Modify: `factcheck_mvp/agente.py` (`_executar_onda`, `criticar` — contar avaliações, não só buscas)
- Test: `tests/test_agente.py`, `tests/test_telemetria.py`

**Interfaces:**
- Consumes: valores atuais `DEEP_CRAWL_TOTAL=10`, `DEEP_CRAWL_TIMEOUT_S=15`, `JUIZ_TIMEOUT_TOTAL_S=600`, `AGENTE_MAX_POR_AFIRMACAO=12`, `SERPAPI_DAILY_CAP=100`
- Produces: crawl+avaliação de `min(relevantes, teto_realinhado)` peças com resultado parcial preservado em timeout/cap; trace com `etapa=avaliador` por peça (`posicao`, `n_chars_trecho`, `corpo_lido`, `metodo`) e `etapa=juiz` agregada

- [ ] **Step 1: Write the failing test** — `test_orcamento_parcial_preservado`: 20 peças relevantes com `DEEP_CRAWL_TIMEOUT_S` estourado no meio; assert que as avaliadas até ali permanecem em `julg` e o trace tem `fallback deep-crawl/avaliador` + `limitacoes` correspondente.
- [ ] **Step 2: Run test to verify it fails** — Run: `python3 -m pytest tests/test_agente.py tests/test_telemetria.py -q` — Expected: FAIL (sem evento `avaliador` por peça nem preservação parcial nesse caminho).
- [ ] **Step 3: Implement tetos + telemetria** — realinhar `DEEP_CRAWL_TOTAL`/`por_afirm` ao "cada relevante" (ex.: `por_afirm = AGENTE_MAX_POR_AFIRMACAO`, total = `3×por_afirm`); `JUIZ_MAX_NOTICIAS` deixa de cortar avaliação (vira teto de exibição em `_fontes`); emitir `telemetria.evento("fonte", estagio="avaliador", ...)` por par e `fallback` em timeout/cap.
- [ ] **Step 4: Run test to verify it passes** — Run: `python3 -m pytest tests -q` — Expected: PASS (suite inteira verde).
- [ ] **Step 5: Commit** — `git add factcheck_mvp/config.py factcheck_mvp/pipeline.py factcheck_mvp/agente.py tests/test_agente.py tests/test_telemetria.py && git commit -m "feat: orcamento e telemetria do avaliador N×1"`
