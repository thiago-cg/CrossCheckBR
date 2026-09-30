# CrossCheckBR — guia para o Claude Code

Checador de fatos PT-BR (`factcheck_mvp/`). Saída: propensão `baixa|media|alta|indeterminada` **a se
tratar de desinformação**. Review completo: `docs/review/RELATORIO.md`. Contrato de telemetria/replay:
`docs/TELEMETRIA.md`. Eval: `eval/README.md`. Python: `python3` (não existe `python`).

## Comandos

```bash
python3 -m pytest tests -q                                  # sempre verde antes e depois
python3 -m eval.run [--modo replay|record] [--split dev] [--nome x] [--gate]
python3 -m factcheck_mvp.cli checar "texto" [--modo replay] [--json]
python3 -m factcheck_mvp.cli trace <run_id|last> [--eventos llm,fallback]
python3 -m factcheck_mvp.cli runs --n 10
```

- Traces: `runs/<run_id>/{trace.jsonl,resultado.json}`. Resultados do eval:
  `eval/resultados/<ts>-<nome>/{relatorio.md,metricas.json,casos.jsonl}`. Baseline: `eval/baseline.json`.
- Cassetes (HTTP+LLM gravados): `eval/cassettes/`. Uso da SerpAPI: `eval/serpapi_uso.json`.

## O loop

1. `python3 -m eval.run --nome <iter>` (replay) → 2. ler `relatorio.md` e `metricas.json`
   (acerto, **erro_grave**, indeterminada, `descoberta`, fallbacks) → 3. `cli trace <run_id>` dos casos
   errados → 4. hipótese sobre a **etapa** culpada (afirmações, busca, deep crawl, juiz, sinais,
   agregador, travas) → 5. mudança pequena → 6. `pytest` + eval de novo → 7. comparar com o baseline
   (delta impresso; `--gate`) → registrar em `eval/ITERACOES.md`: hipótese, mudança, métricas antes/depois,
   run_ids olhados. Só atualize o baseline (`--salvar-baseline`) quando a mudança for aceita.

## Métrica principal e modos

- Principal = perfil **busca aberta** (padrão do eval: `INDICE_CHECAGENS=0`). O atalho do índice é
  métrica secundária: `--env INDICE_CHECAGENS=1 --nome indice`.
- Se `metricas.descoberta.pulada` for alta (sem `SERPAPI_KEY`), o eval **não** julga mudanças de
  busca/juiz. Olhe sempre esse campo antes de concluir algo.
- **replay** (padrão) para iterar em lógica (sinais, agregador, travas, parsing): determinístico, sem rede.
- **record** quando a mudança altera queries, prompts ou páginas lidas (prompt novo = cassete novo;
  record reusa o que já existe e grava o que falta). Em replay, `http_miss_total > 0` = faltou gravar.
- SerpAPI: cada busca live é contada em `eval/serpapi_uso.json`; teto opcional via
  `SERPAPI_ORCAMENTO_RESTANTE` ou `--max-buscas-serpapi`. Erro de cota (HTTP 401/403/429) aparece como
  `fallback` pedindo nova `SERPAPI_KEY` no `.env`: pare e avise o usuário.

## Regras anti-autoengano

- Nunca editar casos, rótulos, `esperado` ou o baseline para "passar". Rótulo errado: corrija com
  justificativa na `nota` e registre em ITERACOES.md.
- Iterar só no split `dev`; rodar `--split holdout` ao fim de uma rodada e reportar os dois.
- Não ajustar pesos/limiares para acertar um caso específico; a mudança deve ter razão geral.
- Todo fallback novo emite `telemetria.fallback(onde, motivo)`; todo I/O novo usa `replay.*`.
- Erro grave (falso→baixa, verdadeiro→alta) pesa mais que acerto.
- Não commitar sem pedido. Não ler `bot.log` (tem segredos). Nunca imprimir chaves do `.env`.

## Ambiente

- LLM local OpenAI-compatível (Unsloth Studio) em `http://127.0.0.1:8888/v1`, modelo carregado hoje:
  `unsloth/LFM2.5-8B-A1B-GGUF`. **Não carregar outro modelo no Studio sem pedir** (troca o do usuário).
  Checar: `curl -s -m 3 http://127.0.0.1:8888/v1/models`.
- `.env` tem `SERPAPI_KEY`; não há `OPENROUTER_API_KEY` → juiz (OpenRouter) cai em fallback léxico
  (aparece como `fallback onde=juiz`/`openrouter.chat` no trace).
- O pipeline grava sites descobertos em `factcheck_mvp/data/catalogo.json`; CLI e eval usam cópia
  temporária por padrão (`--catalogo-real` desliga).
- `/iterar [n]` (`.claude/commands/iterar.md`) roda n rodadas do loop.
