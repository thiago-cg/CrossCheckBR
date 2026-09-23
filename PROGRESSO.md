# Loop 12 — 22/09/2026 — LLM-juiz por notícia (teto 10) + termômetro; Jev só no schema
Status: implementado e verificado live; suite 60 passed; bot reiniciado c/ código novo.
Versão: `mvp-0.2.0`.

## O que mudou (pedido)
- **Jev SÓ no schema** (`descoberta_site.py`): `_classificar_tipo_laya` (quebrado,
  importava `_get_router` inexistente) virou `_classificar_tipo_jev` via Decisions
  (1 noul `e_checagem`); aplicado em `descobrir()` sobre a proposta + CLI
  `--jev` (`--laya` mantido como alias legado). Live OK: Aos Fatos→checagem,
  loja→geral.
- **Julgamento LLM** (`juiz_llm.py`, novo): 1 LLM resume cada notícia relevante
  frente à afirmação (≤3 frases ou `FORA_DO_TEMA`) + **1 juiz em lote** dá o
  **termômetro por notícia (-100..+100)**: +100 sustenta … 0 neutro … -100 refuta.
  `sustenta=|s+|/100`, `refuta=|s-|/100`, `relevante=|s|>=30` (ou flag do juiz).
- **Teto 10** (`JUIZ_MAX_NOTICIAS`): pipeline julga no máximo 10 peças/consulta
  (selos do índice primeiro, depois web); resto cai p/ lexical honesto.
  Resumos em concorrência (`gather`); juiz 1 call por afirmação (teto 120s).
- **Pipeline**: triagem lexical barata nas manchetes → deep crawl top-3 →
  juiz (corpo prevalece) → sinais/tiers mantidos; `resumo_juiz`+`score_juiz`
  em cada `FonteEvidencia`; bot mostra `🌡±N` por fonte; `PESOS["llm-juiz"]=0.05`.
- **Sem LLM** (`usar_llm=False`, cap, rede): tudo lexical honesto (teto 0.55);
  suite segue hermética.
- **Custo/consulta**: ~1 (afirmações) + ≤10 resumos + 1–4 juiz + 1 padrões ≈
  **até ~16 chat calls** vs `LLM_DAILY_CAP=50` → **~3 consultas/dia**. P/ uso real,
  subir `LLM_DAILY_CAP` no `.env` (ex 200).

## Verificação live
- Resumo+termômetro ibuprofeno: `score -100, refuta 1.0, relevante True, motor llm-juiz`.
- Suite: **60 passed** (39 + 21). Bot de fundo reiniciado (pid 12120, 1 poller, sem Conflict).

## Fixes pós-review (bugs do loop 12, corrigidos no ato)
1. **`UnboundLocalError`**: bloco da descoberta referenciava `relevantes` antes da
   atribuição (reordenação do estágio) → caía no `except` e o aviso "corpo
   provisório" sumia. Agora itera `nao_cat[:teto_n]`.
2. **Chaves esparsas no juiz**: lote com `FORA_DO_TEMA` no meio rotulava `[1]`
   mas o modelo devolve `{"0"}` → score perdido. Bloco agora sequencial com
   remap p/ índice original (+ regressão `test_termometro_lote_esparso_remapeia`).
3. **Triagem fatiava antes de ordenar** (`unicas[:12]`): match forte fora do
   top-12 SerpAPI nunca chegava ao juiz. Agora ordena tudo e fatia depois.
4. **Honestidade**: caminho lexical usa `score=None` (não exibe `🌡+0` falso).
   (Nit "resumo descartado" improcedente: resumo é preservado p/ exibição.)
- Suite após fixes: **61 passed**. Bot reiniciado c/ fixes (pid 11288).

## Fixes do log live café/câncer (rajada estourou 429 + vazios do deepseek)
1. **429 auto-infligido**: 10 resumos em concorrência total derrubaram o fallback
   (gemma 429). Semáforo `JUIZ_CONCORRENCIA=4` em `_julgar_lote` (teto segue 10).
2. **Resposta vazia do deepseek** (200 sem conteúdo, ~4×/consulta): `chat()` agora
   repete 1x no MESMO modelo antes de cair p/ o fallback.
- Testes: `test_chat_repete_resposta_vazia_1x_no_mesmo_modelo`,
  `test_concorrencia_resumos_limitada`. Suite: **63 passed**.
  Bot reiniciado c/ fixes (1 poller, sem Conflict).
- Não mexido (pré-existente): LLM local `127.0.0.1:8888` devolve 400 no caminho
  de padrões (investigar payload fora deste loop).

## Caso "água da torneira" (BAIXA com fontes fora do tema) — CORRIGIDO
- Causa: selo VERDADEIRO sobre golpes + páginas institucionais contavam como
  "cobertura" e ancoravam corpo → BAIXA. O juiz marcava irrelevante, mas nada
  filtrava.
- Fix: `FonteEvidencia.relevante` (True/False do juiz ou fallback; None =
  não julgado). **Só `relevante is True` decide veredito**: "cobertura ampla" e
  `tem_corpo` exigem relevância julgada; julgado-irrelevante sai do lado a lado
  (bot + header) e vai p/ o fim da lista (resto auditável). Não-julgadas seguem
  visíveis, sem decidir.
- Resultado esperado agora: INDETERMINADA + "Sem evidência relevante (nada
  sobre o tema nas fontes consultadas)".
- Teste: `test_irrelevantes_nao_viram_cobertura_nem_veredito`. Suite: 64 passed.
  Bot reiniciado c/ fix.

## Pendente
- Subir `LLM_DAILY_CAP` p/ uso real (decisão do dono; .env local).
- Fix opcional `lexico-top3` ("cobertura ampla") segue pendente.
- `br_news_crawler.py --classifier laya` é legado CLI, fora do pipeline.

## Caso "Café cura câncer" (bug BAIXA) — RESOLVIDO via contenção honesta
- Antes (Jev): propensão **BAIXA** enganosa (sugeria "provavelmente ok").
- Agora (juiz, live): **INDETERMINADA** + "elementos insuficientes — compare as
  fontes". Correto: nenhuma fonte trata da *cura* (só de *risco*/temperatura);
  o juiz não sustentou nem refutou, e o sistema conteve em vez de alucinar.
  Funcionou: corpo lido c/ quote, descoberta (+2 sites), sem "cobertura ampla"
  fantasma, placeholder declarado.
- Cosmético menor: quote cortado no meio da palavra (fatia de 140 chars).

---
# Loop 9/10 — 22/09/2026 — swap laya→jev + fixes de teste + bot no ar
Status: implementado e verificado live; suite em lotes verde (55 passed).
Versão: `mvp-0.2.0`.

## O que mudou (pedido: laya → `~typesafe/jev-latest` via OpenRouter)
- **Motor de implicação** (`implicacao.py`): laya substituído por **Jev** na
  Decisions API da OpenRouter (`POST /api/alpha/decisions`, NÃO chat/completions).
  - `JEV_MODEL = "typesafe/jev-latest"`; state `{afirmacao, fonte}`;
    `_PERGUNTAS` é **dict** de 3 `noul` (`sustenta`, `refuta`, `relevante`) —
    array → HTTP 400.
  - `motor: "jev"` no resultado; `_fallback_lexico` preservado se Jev falhar.
- **`llm_openrouter.py`**: `decisions(model, state, questions)` +
  `_URL_DECISIONS`; timeout via `JEV_TIMEOUT_S=12` (`config.py`).
- **Integração**: `pipeline.py` mensagens "jev"; `agregador.py`
  `PESOS["jev"]=0.05`; `schemas.py`/`__init__.py` docstrings;
  `descoberta_site._classificar_tipo_laya` degrada com try/except.
- **Teste**: `tests/test_review.py::test_jev_decisions_chamavel_duas_vezes`.
- **Laya ainda presente** só em `descoberta_site` (degradação) e
  `br_news_crawler.py --classifier laya`.

## Verificação live
- `decisions(JEV_MODEL, state, _PERGUNTAS)` → 2.69s fria / 1.21s quente;
  answers `sustenta 0.29, refuta 0.02, relevante 0.97`;
  modelo `typesafe/jev-1.13-20260917`, provider TypeSafe.
- `implicacao()` rumor ibuprofeno → 1.78s, `motor: "jev"`, `erro: None`.
- Sem `TYPESAFE_API_KEY`; mesmo `OPENROUTER_API_KEY`.

## Fixes de teste (SIGINT do sandbox)
- Diagnóstico: sandbox/env manda **SIGINT assíncrono** após
  `os.kill(pai,0)` + `os.kill(pid_morto,0)` — "trava" em `open(...,'w')` é só
  onde o SIGINT cai (não é file lock).
- `tests/test_guard_unica.py` e `tests/test_bot_modelo.py`: mock de `os.kill`
  nos testes de instância única → verdes.
- Debug temporário `tests/test_guard_debug.py` removido.

## Bot (@fn_tic_bot)
- Instância antiga (pid 11796) morta; `bot.lock` recriado.
- **No ar**: `cmd /c rodar_bot_task.bat` → stub venv (22368) → interpretador
  real **29952** (dono do lock, 18 threads, `getUpdates` 200 OK contínuo,
  **sem Conflict**). `schtasks` falhou no sandbox (aspas/senha NonInteractive)
  — fallback `Start-Process` do bat resolveu.
- `bot_err.log` ativo; `bot.lock` = `{"pid": 29952}`.

## Testes
- **55 passed** em lote único (~4s): review, review_loop4, schemas, pipeline,
  guard_unica, bot_modelo, serpapi_layer, regressao_36, loop1, indice,
  descoberta_site.

## Pendente (diagnóstico, não feito — pedido anterior cancelado pelo pivot)
- Bug "Café cura câncer" → BAIXA: (1) `lexico-top3` não conta "cobertura ampla"
  quando `sus/ref < 0.3` (`pipeline.py:279`); (2) corpo lido sem
  `max(sus,ref)>=0.3` descarta fonte; (3) subir timeout Jev se frio >12s.
- Claim vago → pergunta interativa; mostrar +3 fontes no Telegram.

## 7 checks
| # | Check | Status | Evidência |
|---|---|---|---|
| 1 | Veredito em segundos + 2-3 fontes lado a lado | ok-parcial | `header`+`why_1linha`; Jev ~1.2–2.7s |
| 2 | Prova corpo lido, não só manchete | ok | deep crawl + descoberta; `tests/test_descoberta_site.py` |
| 3 | Recibo visível da novidade | ok | `etapas` com motor real; `versao mvp-0.2.0` |
| 4 | Sem regressão nos 36 artigos | ok | `tests/test_regressao_36.py` |
| 5 | Testes verdes + validado live | ok | 55 passed; live Jev + bot no ar |
| 6 | Neutro, fato vs opinião/sátira | ok | `verificar_neutralidade()` |
| 7 | Crawl safety + cost caps | ok | allow-list+SSRF; caps SerpAPI/LLM/deep-crawl/rate-limit |

## Próximos
- Opcional: fix (1) do bug "cobertura ampla" (`lexico-top3`).
- Re-run full suite num ambiente sem SIGINT agressivo (contornado por lotes).
- Atualizar `docs/progresso/` screenshots.
