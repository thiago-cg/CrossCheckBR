# eval/ — harness de avaliação

```bash
python3 -m eval.run                                  # replay, split dev, perfil busca aberta
python3 -m eval.run --modo record --nome x           # grava cassetes faltantes (rede + LLM)
python3 -m eval.run --split holdout                  # só no fim de uma rodada de iterações
python3 -m eval.run --filtro-tag negacao,caixa       # subconjunto por tag
python3 -m eval.run --ids cafe_cura_cancer --modo replay
python3 -m eval.run --env INDICE_CHECAGENS=1 --nome indice   # métrica secundária: atalho do índice
python3 -m eval.run --gate                           # exit 1 se piorar vs baseline
```

## Casos (`eval/casos*.jsonl`, 1 JSON por linha; `#` = comentário)

```json
{"id": "cafe_cura_cancer",
 "entrada": {"tipo": "titulo", "conteudo": "Café cura câncer"},
 "rotulo": "falso",
 "esperado": ["alta"], "aceitavel": ["media"],
 "tags": ["saude", "adversarial"], "split": "dev", "origem": "sonda", "nota": "por que o rótulo"}
```

| campo | obrigatório | valores |
|---|---|---|
| `id` | sim | único entre todos os arquivos (duplicado: vale o 1º, com aviso) |
| `entrada` | sim | `{tipo: texto\|titulo\|link, conteudo}` (vira `EntradaConsulta`) |
| `rotulo` | sim | `falso \| enganoso \| verdadeiro \| sem_evidencia` — verdade do que **a entrada afirma** |
| `esperado` | não | níveis que contam como acerto; padrão pelo rótulo (abaixo) |
| `aceitavel` | não | acerto parcial; padrão pelo rótulo |
| `tags` | não | `adversarial`, `negacao`, `relato`, `caixa`, `satira`, `vago`, `fora_do_indice`, `no_indice`… |
| `split` | não | `dev` (padrão, iterar aqui) \| `holdout` (só conferir no fim) |
| `origem` | não | `sonda \| claimreview \| manual` |
| `nota` | não | justificativa do rótulo |
| `data_referencia` | não | `YYYY-MM-DD` = "hoje" do texto p/ relevância temporal (E4); vira `EntradaConsulta.data_referencia` |

Padrão = relógio gravado no record (reproduz o que o sistema fez ao vivo no dia do
record): em `replay` o pipeline lê a data do cassete `relogio://hoje`; cassete antigo
sem relógio → E4 desligado com `fallback onde=relogio`. O campo `data_referencia` no
caso é só quando o texto descreve um momento conhecido (ex.: = `data_checagem` de um
caso ClaimReview), com justificativa na `nota`, decidido **antes** de rodar e registrado
em `ITERACOES.md`; nunca mexer depois de ver o resultado.

Mapeamento padrão (nível = propensão **a se tratar de desinformação**):

| rótulo | esperado | aceitável (parcial) | erro grave |
|---|---|---|---|
| falso / enganoso | alta | media | baixa |
| verdadeiro | baixa | — | alta |
| sem_evidencia | indeterminada | — | — |

Negação: "É falso que a vacina altera o DNA" afirma algo **verdadeiro** → rótulo `verdadeiro`,
esperado `baixa`. O rótulo é sempre sobre o conteúdo da entrada, não sobre o boato citado.

Arquivos: `casos.jsonl` (sondas dos relatórios `docs/review/r1_agentes.md` e `r3_decisao.md`);
outras fases adicionam arquivos `casos_<origem>.jsonl` (ex. `casos_claimreview.jsonl`) ou fazem append
no mesmo formato. Todos os `eval/casos*.jsonl` são lidos por padrão (`--casos` aceita arquivos/globs).
**Nunca edite rótulos/esperados para "passar"** — só para corrigir um rótulo errado, com a razão na `nota`.

## Métricas (`eval/resultados/<ts>[-nome]/metricas.json`)

- `acerto` (nível ∈ esperado), `acerto_com_parcial`, `erro_grave` / `n_erro_grave`,
  `taxa_indeterminada`, `niveis`; os mesmos blocos `por_rotulo` e `por_tag`
  (`destaque_indice` = tags `fora_do_indice`/`no_indice`).
- `descoberta`: fração de casos em que a descoberta web **rodou** × **pulada** (sem chave) × ausente.
  Se a maioria for `pulada`, o eval não mede busca/juiz.
- `taxa_fallback_por_onde` (fração de casos com ≥1 fallback naquele ponto),
  `fallbacks_total_por_onde`, `descartes_por_motivo`.
- `llm_chamadas_total`, `llm_chamadas_por_caso`, `latencia_p50_ms`, `latencia_p95_ms`,
  `n_erros_execucao`, `http_miss_total` (em replay: requisições sem cassete).
- `serpapi`: `live_no_eval` (buscas live gastas neste eval), `usadas_total`, `restantes`/`orcamento`
  (só se houver teto).
- `meta`: modo, split, filtros, `env` (overrides aplicados), git sha, arquivos de casos.
- `delta` vs `eval/baseline.json` calculado **só sobre os ids em comum** (casos novos não distorcem),
  com a lista de casos que mudaram de nível.

`casos.jsonl` do resultado: por caso `id, rotulo, nivel, esperado, ok, parcial, grave, run_id,
dur_ms, n_llm, n_fallbacks, fallbacks_por_onde, descoberta, serpapi_live, erro`.
`relatorio.md`: tabela legível + "Para investigar" com `python3 -m factcheck_mvp.cli trace <run_id>`.

## Flags

| flag | efeito |
|---|---|
| `--modo replay\|record\|live` | padrão `replay` (sem rede; faltou cassete → miss contado em `http_miss_total`) |
| `--split dev\|holdout\|todos` | padrão `dev` |
| `--casos A B…` | arquivos/globs; padrão `eval/casos*.jsonl` |
| `--filtro-tag a,b` / `--ids a,b` | subconjunto (ids ignoram o split) |
| `--env CHAVE=VALOR` | repetível; aplica em `os.environ` e em `factcheck_mvp.config` (tipo preservado) só durante o eval; padrão `INDICE_CHECAGENS=0` |
| `--nome x` | sufixo do diretório de resultado |
| `--paralelo N` | casos simultâneos (padrão 1) |
| `--timeout-caso S` | padrão 300 s |
| `--max-buscas-serpapi N` | teto de buscas live neste eval; não inicia caso que poderia estourar |
| `--salvar-baseline` | grava `eval/baseline.json` |
| `--gate [--gate-pp 5]` | exit 1 se `n_erro_grave` subir ou `acerto` cair mais que N pp vs baseline |
| `--catalogo-real` | por padrão o eval usa cópia temporária do `catalogo.json` (o pipeline atual grava nele) |

Perfis: **principal** = busca aberta (`INDICE_CHECAGENS=0`, padrão). **Secundário** = atalho do
índice (`--env INDICE_CHECAGENS=1 --nome indice`).

## A/B da descoberta (agente × estratégias estáticas)

O A/B é feito **pelo eval** (casos rotulados = gold), não por métrica de URL: o antigo
`scripts/ab_descoberta.py` media "achou host de checagem" com o mesmo critério do crítico
(circular) e foi removido. Mesmos casos, mesma seed de cassetes; só muda `SERP_ESTRATEGIA`:

```bash
# 1ª vez (queries/prompts novos): grava cassetes; cada perna gasta SerpAPI + LLM
python3 -m eval.run --modo record --env SERP_ESTRATEGIA=agente   --nome ab-agente
python3 -m eval.run --modo record --env SERP_ESTRATEGIA=avancada --nome ab-avancada
# depois, determinístico (sem rede):
python3 -m eval.run --env SERP_ESTRATEGIA=agente   --nome ab-agente
python3 -m eval.run --env SERP_ESTRATEGIA=avancada --nome ab-avancada
# (opcional) só onda 1 do agente, p/ isolar o efeito do crítico:
python3 -m eval.run --env SERP_ESTRATEGIA=agente --env AGENTE_MAX_ONDAS_EXTRAS=0 --nome ab-agente-sem-extra
```

Compare os `metricas.json` (`acerto`, `n_erro_grave`, `taxa_indeterminada`, `serpapi.live_no_eval`,
`llm_chamadas_por_caso`, `latencia_p95_ms`) e confira `descoberta.pulada ≈ 0` nas duas pernas.
`avancada` = 2 buscas/afirmação (crua + `site:` das agências do catálogo), sem crítico; `agente` =
as mesmas 2 da onda 1 + crítico pós-juiz com até `AGENTE_MAX_ONDAS_EXTRAS` ondas reescritas pelo
LLM (teto `AGENTE_MAX_BUSCAS` por caso). No trace: `cli trace <run_id> --eventos agente,etapa`.

## Medição do E4 (relevância temporal)

`metricas.json` tem o bloco `e4` (também no nível 0, via `eval.decisao`):

| campo | o quê |
|---|---|
| `casos_com_marcador` | casos cujo texto/afirmação tem marcador temporal relativo ("hoje", "ontem"…) ou data explícita do fato (marco, Task 4b: "dia 8", "8 de outubro") |
| `casos_com_desconto` | casos com ≥1 fonte descontada (`descontos_temporais` não vazio) |
| `casos_nivel_mudou` | casos em que o desconto mudou o nível (`nivel != nivel_sem_desconto`, só conta com desconto real) |
| `bits_descartados_total` | soma dos `bits_descartados` (força da evidência removida por ser de outro episódio) |
| `referencia_ausente` | casos sem `data_referencia` (E4 sem âncora: não mede) |

Fallbacks do E4 no trace (contrato completo em `docs/TELEMETRIA.md`, §1):

| `onde` | motivo | efeito |
|---|---|---|
| `relogio` | `data de referência não gravada` · `cassete sem data` | E4 desligado no caso (`referencia_ausente`) |
| `data_pub` | `formato não reconhecido` · `sem âncora` (data relativa sem âncora) | a fonte fica sem data e sem desconto temporal |
| `config` | aviso de `E4_*` inválido (não é inteiro > 0) | usa o default, com rastro |

O evento `fonte` com `estagio=aplicabilidade` (1 por par peça × afirmação na fase base) traz `janela`,
`marco`, `excedente` e `referencia`. `_e4_do_trace` lê `janela` e `marco` por afirmação do evento
`evidencias`, o mesmo critério do `marcador` de `eval.decisao`.

A/B honesto sem flag no código de produto — a perna controle zera `texto_usuario`/`janela`
antes do `decidir` (o E4 desliga por construção, pois `dias_excedentes` não mede sem marcador):

```bash
python3 -m eval.decisao --snapshot eval/snapshots/e4-sub20.jsonl            # com E4
python3 -m eval.decisao --snapshot eval/snapshots/e4-sub20.jsonl --sem-e4   # controle
```

Snapshot a partir de um eval de pipeline (Task 8: traces com evento `evidencias`):

```bash
python3 -m eval.decisao --gerar-snapshot --resultado eval/resultados/<ts>-nome \
  --saida eval/snapshots/e4-sub20.jsonl
```

(lê `casos.jsonl` do resultado → `run_id` → evento `evidencias` do trace via
`decisao_gerar.evidencias_do_trace`; pula casos sem run ou sem evento `decisao`.)
Trace antigo, sem `evidencias`: a linha é **reconstruída** do evento `decisao` (sem
`texto_usuario`/`data_referencia`, então E4 não vale nela). A origem do selo vem do próprio trace
(`selo X (pagina|indice)` no voto); selo que entrou em conflito ou foi aplicado sem aparecer no voto
sai como `indice` (`conflitos`/`vereditos_aplicados`); sem registro, cai em `pagina` (default
documentado). O comando informa os reconstruídos à parte.
**Atenção:** a reconstrução de traces antigos pode DIVERGIR da decisão original. O trace não guarda a
origem de todo selo (um selo de origem desconhecida vota igual a `indice`, mas o default `pagina` não
vota) e não guarda `texto_usuario` nem `data_referencia` (o E4 não vale nela). Para medir decisão com
fidelidade, use os snapshots de runs com evento `evidencias`.

Testes do harness: `tests/test_eval_harness.py` (Pipeline falso, sem rede).
