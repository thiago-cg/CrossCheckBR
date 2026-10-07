# Telemetria, replay e eval — contrato

Infra para rodar o pipeline, observar o que aconteceu em cada etapa e comparar mudanças de forma
determinística. Três peças:

| Peça | Arquivo | Para quê |
|---|---|---|
| Telemetria | `factcheck_mvp/telemetria.py` | trace JSONL por execução (`runs/<run_id>/`) |
| Replay | `factcheck_mvp/replay.py` | grava/reproduz HTTP e LLM (`eval/cassettes/`) |
| Eval | `eval/run.py` + `eval/casos*.jsonl` | métricas por caso rotulado, baseline e gate |

CLI: `python3 -m factcheck_mvp.cli {checar,trace,runs}` (ver `CLAUDE.md`).

## 1. Telemetria

```python
from factcheck_mvp import telemetria

rid = telemetria.iniciar_run({"entrada": "...", "tipo": "titulo"})  # ou: with telemetria.run(meta) as rid:
telemetria.evento("etapa", nome="afirmacoes", status="ok", detalhe="1 afirmação")
telemetria.fallback("juiz", "OPENROUTER_API_KEY ausente")          # atalho p/ evento fallback
with telemetria.span("deep-crawl", n=3) as sp:                       # também `async with`
    sp.dados["lidos"] = 2                                            # vai no span_fim
with telemetria.finalidade("juiz"):                                  # rotula chamadas LLM do bloco
    ...
telemetria.finalizar_run(relatorio.model_dump(mode="json"))
```

- Estado em `contextvars`: cada task asyncio tem seu run; `asyncio.to_thread` herda o run.
- Sem run ativo, `evento` é no-op. `iniciar_run` é **reentrante**: se já há run (o eval abre um
  por caso), reusa o id e só o `finalizar_run` mais externo fecha.
- `Pipeline.executar` abre/fecha o run sozinho (o nome na especificação era `checar`).
- Ambiente: `TELEMETRIA=0` desliga; `TELEMETRIA_DIR` (padrão `runs/` na raiz). Sob pytest fica
  desligada, salvo `TELEMETRIA` explícita (testes usam `tmp_path`).
- Antes de gravar: redação de segredos (`api_key=`, `key=`, `token`, `/bot<token>/`,
  `Authorization`/`Bearer`, `sk-...` e os valores de `SERPAPI_KEY`/`OPENROUTER_API_KEY`/
  `TELEGRAM_TOKEN` do ambiente) e truncamento de strings em 2000 chars (`…[+N chars]`).
- Nunca levanta: falha de disco vira silêncio.

### Arquivos

`runs/<run_id>/trace.jsonl` — uma linha por evento:

```json
{"ts": "2026-09-28T19:52:59.256+00:00", "t_rel_ms": 6491.3, "tipo": "llm", "dados": {...}}
```

`runs/<run_id>/resultado.json` — `{run_id, inicio, fim, dur_ms, erro, meta, relatorio, metricas}`,
onde `relatorio` é o `RelatorioChecagem` final e `metricas` =
`{eventos_por_tipo, n_llm, n_llm_erro, llm_latencia_total_ms, llm_por_finalidade, n_http, http_cache,
n_fallbacks, fallbacks_por_onde, descartes_por_motivo, n_erros, dur_ms, nivel}`.

`run_id` = `AAAAMMDD-HHMMSS-<6 hex>` (ordena por tempo; `last` resolve o mais recente).

### Tipos de evento (campos de `dados`)

| tipo | campos | quem emite hoje |
|---|---|---|
| `run_inicio` | meta: `entrada`, `tipo`, `usar_llm`, `config{IO_MODO, SERP_ESTRATEGIA, SERP_ENGINE, serpapi_ativo, openrouter_ativo, OPENROUTER_MODEL, UNSLOTH_*, detector, JUIZ_MAX_NOTICIAS, LLM_DAILY_CAP}` (+ `caso_id`, `rotulo`, `esperado` no eval) | `Pipeline.executar`, eval |
| `run_meta` | meta extra de um `iniciar_run` aninhado | idem |
| `run_fim` | `dur_ms`, `ok`, `erro`, `nivel` | `finalizar_run` |
| `etapa` | `nome`, `status` (ok\|parcial\|falha\|pulada), `detalhe`, `n_fontes`. Com `nome=agente-onda` (1 por busca do agente; só no trace, não no recibo): `onda`, `afirmacao`, `query`, `motivo` (consulta\|agencias\|reformulada), `n_resultados`, `n_novos`, `serpapi` (ok\|vazio\|cap\|erro\|timeout\|reuso), `cache`, `reuso` | `etapa()` do pipeline; `agente._executar_onda` |
| `agente` | decisão do crítico pós-juiz, 1 por afirmação por rodada: `afirmacao`, `decisao` (parar\|nova_onda), `motivo`, `contagens{SUSTENTA, REFUTA, RELATA_SEM_ENDOSSO, NAO_TRATA, sem_juiz, clusters_postura, veredito_aplicavel}`, `extras_feitas`, `buscas_restantes` | `agente.criticar` |
| `llm` | `motor` (llm-local\|openrouter\|openrouter-decisions), `modelo`, `finalidade`, `latencia_ms`, `prompt_sha`, `n_chars_prompt`, `saida` (truncada), `erro`, `cache` (live\|hit\|miss), `status` | `replay.llm_post` |
| `http` | `metodo`, `url` (redigida), `status`, `bytes`, `latencia_ms`, `cache` (live\|hit\|miss), `erro`, `truncado`, `gravado` | todo wrapper do `replay` |
| `fonte` | `url`, `estagio` (deep-crawl\|descoberta-site\|relatorio), `decisao` (mantida\|descartada), `motivo` (+ `tipo_fonte`, `corpo_lido`, `confianca` no estágio relatorio) | aprofundar, descoberta_site, `Pipeline.executar` |
| `fallback` | `onde`, `motivo` (+ extras) | ver tabela abaixo |
| `sinal` | `motor`, `rotulo`, `valor`, `confianca`, `direcao` (via `agregador._direcao`, `None` se não existir), `peso` (`agregador.PESOS`), `evidencias` | pipeline, antes de `agregar` |
| `decisao` | `nivel` (final), `nivel_agregador`, `score`, `sinais` (lista `motor:rotulo`), `why`, `travas{opiniao, vago, rumor, tem_veredito, tem_corpo, n_corpo}` | pipeline (fim, e no retorno "sem afirmação") |
| `erro` | `onde`, `erro` | pipeline (exceção), replay (gravar cassete), eval |
| `span_inicio` / `span_fim` | `nome`, extras; no fim `dur_ms`, `ok`, `erro` | quem usar `span` |

`finalidade` de LLM: explícita (`llm_post(finalidade=...)` ou `with telemetria.finalidade(...)`) ou
inferida do chamador: `afirmacoes`, `padroes`, `reformular` (agente: consulta da onda extra), `juiz-resumo` (`juiz_llm.resumir`), `juiz`
(`juiz_llm.termometro`), `decisions-tipo-site`, senão `modulo.funcao`.

### Pontos de `fallback` instrumentados (`onde`)

`afirmacoes`, `afirmacoes.openrouter`, `padroes`, `padroes.openrouter`, `openrouter.chat` (cada
modelo que falhou; teto diário), `juiz` (item julgado por fallback léxico; timeout 120s),
`juiz.lexico-top3` (C1 do review: 3 fontes marcadas relevantes sem leitura), `serpapi`
(erro de rede/HTTP; `motivo="orcamento"` quando o teto recusou; cota da conta esgotada),
`serpapi.cap` (teto diário interno), `serpapi.agencias` (catálogo indisponível: sem `site:` de agências),
`agente` (onda passou de `AGENTE_TIMEOUT_S`, parcial preservado; falha nas ondas extras),
`agente.reformular` (LLM de reformulação falhou → consulta determinística), `deep-crawl`, `aprofundar`,
`descoberta-catalogo`, `descoberta-site`, `descoberta-site.jev`.

**Regra:** todo fallback novo deve chamar `telemetria.fallback(onde, motivo)`.

### Leitura

- `telemetria.ler_run(id|"last") -> {run_id, dir, eventos, resultado, metricas}` (KeyError se não existe)
- `telemetria.resumir_run(id)` → texto compacto: etapas (status, detalhe, Δt desde a etapa anterior),
  chamadas LLM, HTTP com erro, fallbacks agrupados, fontes mantidas/descartadas por motivo,
  sinais com direção/peso, decisão (nível final × agregador × travas).
- `telemetria.listar_runs(n)`, `telemetria.eventos_de(id, ["llm","fallback"])`.

## 2. Replay (I/O determinístico)

Modo: env `IO_MODO=live|record|replay` (padrão `live`), `replay.definir_modo(m)` (processo) ou
`with replay.modo(m):` (contexto async-safe). Precedência: contexto > definir_modo > env.

| modo | comportamento |
|---|---|
| `live` | rede de verdade, nada gravado |
| `record` | se já há cassete, **reusa** (não chama); senão chama e grava |
| `replay` | só cassete; faltou → `ReplayMiss` (subclasse de `replay.HTTPError`/curl_cffi) + evento `http` com `cache=miss` |

Wrappers (todos emitem `http`; `llm_post` também `llm`):

```python
replay.http_get(url, params=None, **kw) -> Resposta            # ~curl_cffi get
replay.http_post(url, json=None, **kw) -> Resposta             # ~curl_cffi post
await replay.ahttp_get(url, max_bytes=None, headers=None, timeout=10.0, follow_redirects=False)
replay.llm_post(url, payload, headers=None, timeout=60, motor="", finalidade=None, extrair=None)
```

`Resposta`: `status_code`, `headers` (case-insensitive), `content` (bytes), `text`, `url` (final),
`json()`, `raise_for_status()` (levanta `ErroStatusHTTP`, subclasse de `replay.HTTPError`), `cache`,
`truncado`. `ahttp_get` **não segue redirect**: devolve o 3xx com `headers["location"]` para o chamador
revalidar o próximo hop (semântica anti-SSRF do aprofundar/descoberta), e lê o corpo em streaming até
`max_bytes`. Em live, os wrappers chamam `curl_cffi.requests.get/post/AsyncSession` pelo atributo
do módulo (com `impersonate="chrome"` por padrão), então
`monkeypatch.setattr(curl_cffi.requests, "get", ...)` nos testes continua valendo.

Cassetes: `eval/cassettes/<sha256>.json` (`CASSETES_DIR`). Chave = sha256 de
`{método, URL canônica (sem api_key/key/token…, query ordenada, sem fragmento), corpo JSON}`. Para LLM
o corpo é o payload inteiro (modelo, mensagens, temperatura, max_tokens): mudou o prompt ⇒ miss. Para
SerpAPI a chave é só a query (q, engine, hl, gl, num…) — estável entre versões de código. Cassete
gravado com `max_bytes` menor que o pedido e truncado ⇒ miss (regrave). Requisições que falharam
por rede no record (timeout, SSL) não viram cassete: em replay aparecem como miss, com o mesmo efeito. Para regravar, apague o arquivo.
Headers de requisição nunca são gravados. Em replay, `aprofundar._ip_privado` não resolve DNS (o
cassete só existe se a URL passou no SSRF ao gravar).

Pontos migrados: `afirmacoes` (2), `padroes_llm` (2), `llm_openrouter` (chat + decisions),
`serpapi_layer` (1), `aprofundar._baixar`, `descoberta_site._fetch_aberto`. **Código novo que faz I/O
deve usar esses wrappers** (fora deles não há replay nem telemetria).

### SerpAPI: contagem e cota

- Toda busca live é contada em `eval/serpapi_uso.json` (`total_live`, `por_dia`, `por_run`,
  `chamadas[]`); cassete não conta. `replay.uso_serpapi()` devolve o resumo.
- Teto opcional: `SERPAPI_ORCAMENTO_RESTANTE=N` (total no arquivo) e `replay.definir_teto_serpapi(n)`
  (só o processo; `eval.run --max-buscas-serpapi N`). Estourou → `OrcamentoSerpAPIEsgotado` +
  `fallback(onde="serpapi", motivo="orcamento")`. Sem a env, não há teto.
- HTTP 401/403/429 da SerpAPI (conta sem buscas / chave inválida) → `CotaSerpAPIEsgotada` +
  `fallback` pedindo nova `SERPAPI_KEY` no `.env`; a resposta não vira cassete.

## 3. Eval

Ver `eval/README.md` (formato dos casos, métricas, flags). Cada caso vira um run de telemetria; o
`relatorio.md` lista o comando `cli trace <run_id>` de cada caso errado.
