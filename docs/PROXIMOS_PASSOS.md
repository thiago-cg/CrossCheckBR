# Próximos passos — guia para uma nova sessão do Claude Code

> Leia este arquivo inteiro antes de agir. Depois leia `CLAUDE.md` (regras do projeto, valem sempre),
> `docs/review/REVIEW_2.md` (diagnóstico atual) e `eval/ITERACOES.md` (histórico medido).
> Escrito em 2026-09-30, ao fim da sessão que implementou o PR #4 e fez o Review 2.

## 1. Onde estamos

- `main` @ `17fc2bd`: PR #4 mergeado (telemetria, replay, eval, dados ClaimReview/RSS, extração em cascata,
  `decidir()` em log-odds, juiz de 4 classes, agente com crítico pós-juiz). `pytest`: 319 passed.
- **Não commitados** (commite primeiro, numa branch nova): `docs/review/REVIEW_2.md`, `docs/review/review2/`
  (3 relatórios + `sondas/`), este arquivo.
- Último resultado medido (Iteração 2, dev 70 casos, `gpt-6-luna` geral + `deepseek-v4.1-flash` juiz):
  acerto 20/70, erro grave 3/70 (real: 1/70), indeterminada 27/70. **Mas "sempre alta" acerta 58/70**: o eval
  atual não distingue o sistema de um chute. Isso é o primeiro a consertar.

## 2. Ambiente (não imprima segredos)

- `.env` (fora do git) já tem: `SERPAPI_KEY` (plano grátis, ~100 buscas restantes em 30/09),
  `OPENROUTER_API_KEY`, `OPENROUTER_MODEL=openai/gpt-6-luna`, `OPENROUTER_MODEL_JUIZ=deepseek/deepseek-v4.1-flash`,
  `OPENROUTER_REASONING_EFFORT=low`, `OPENROUTER_RPM=0`, `SERPAPI_DAILY_CAP=1000`, `LLM_DAILY_CAP=2000`.
  Saldo da SerpAPI sem gastar busca: `curl -s "https://serpapi.com/account.json?api_key=$K"` (nunca ecoe `$K`).
- LLM local: Unsloth Studio em `127.0.0.1:8888` (`unsloth/LFM2.5-8B-A1B-GGUF`). O usuário autorizou trocar o
  modelo carregado, mas a API de carga exige login: peça ao usuário para carregar pela interface.
- Cassetes: `eval/cassettes/` (~700 MB, **só nesta máquina**, fora do git). Traces: `runs/` (fora do git).
- Push: o `credential.helper` global aponta para um `gh` que não existe. Use, sem alterar a config:
  `git -c credential.helper= -c "credential.helper=!$(which gh) auth git-credential" push`
- Commits terminam com a linha `Co-Authored-By` indicada pelo sistema. Não commitar sem pedido do usuário
  (o usuário já pediu commits+PR para este trabalho: uma branch por fase, PR para `main`).

## 3. Política de custo (o usuário pediu explicitamente)

Rodadas pagas ficaram caras. Siga a escada; **suba de nível só com aprovação do usuário**:

| Nível | O quê | Custo |
|---|---|---|
| 0 | eval de decisão offline: reexecuta `decidir()` sobre evidências gravadas | zero, segundos |
| 1 | `python3 -m eval.run --modo replay` | zero (misses não gastam) |
| 2 | busca congelada, LLM ao vivo (mudança de prompt do juiz) | só OpenRouter (centavos) |
| 3 | `--modo record` na subamostra fixa de 20 casos (ver `review2/r_eval.md`) | ~50 buscas SerpAPI |
| 4 | dev completo + holdout | ~170 buscas + LLM; só em marcos |

Nunca agende checagens periódicas longas (`ScheduleWakeup`) para acompanhar eval: rode em background e
consulte quando o usuário pedir. Nunca rode eval `record/live` sem dizer o custo estimado antes.

## 4. Plano, em ordem

### Fase A — consertar o eval (nível 0–1; nada disso muda o sistema)

- [ ] **A1. Métricas que batem o trivial** (`eval/run.py`): acurácia balanceada por rótulo; erro grave por direção
  (falso/enganoso→baixa e verdadeiro→alta separados); cobertura = 1 − indeterminada; precisão por nível; AUC
  ordinal; imprimir sempre as linhas de referência **"sempre alta"** e **"sempre média"**; ICs de Wilson.
  `--gate` passa a exigir acurácia balanceada ≥ a de "sempre alta" e erro grave ≤ baseline.
  Corrigir o "parcial" para bater com o `eval/README.md` (hoje falso→média nunca conta; enganoso→média conta pleno).
- [ ] **A2. `_descoberta_status`** (`eval/run.py:186`): hoje `startswith("descoberta")` inclui a etapa
  `descoberta-catalogo` (`pipeline.py:696`) e pode dizer "rodou" sem busca web. Filtrar para
  `("descoberta", "descoberta-agente")`. Teste.
- [ ] **A3. Eval de decisão (nível 0).** Emitir evento `evidencias` (o objeto `decisao.Evidencias` serializado)
  imediatamente antes de `decidir()` no pipeline. Criar `python3 -m eval.decisao --snapshot eval/snapshots/<x>.jsonl`
  que reexecuta só `decidir()` e produz as mesmas métricas da A1. Gerar o primeiro snapshot a partir dos traces
  atuais — `docs/review/review2/sondas/extrair.py` + `reconstruir.py` já fazem isso (reproduzem 69/69 níveis);
  transforme em código de verdade. **O snapshot (JSONL pequeno) vai para o git**; `runs/` não.
- [ ] **A4. Replay confiável**: ordenar os lotes do juiz por URL antes de montar o prompt (hoje depende da ordem
  assíncrona → 1/70 diverge); miss de LLM em replay marca o caso `nao_reproduzido` e o tira do delta, em vez de
  virar `indeterminada` em silêncio.
- [ ] **A5. Cota SerpAPI esgotada interrompe o eval** (`serpapi_layer.py:346-351` engole a exceção), conforme CLAUDE.md.
- [ ] Registrar em `eval/ITERACOES.md` a "Iteração 2 re-medida" com as métricas novas (nível 0), incluindo as
  linhas de "sempre alta/média". Commit + PR "Fase A".

### Fase B — correções de decisão (nível 0; validar no eval de decisão)

Cada item: teste de comportamento que falha antes e passa depois; rodar o eval de decisão; registrar delta.
Números de referência já simulados estão em `REVIEW_2.md §3`.

- [ ] **B1. Selo só vale com postura e de checador** (`decisao.py:53` `CLASSES_TRATA`, `:207-217`): selo de página
  que o juiz marcou `RELATA_SEM_ENDOSSO` não vota; selo só de publisher checador curado (ClaimReview de
  **template** do SBT News causou o erro grave `cr_jn_soltura_vorcaro`). Reescrever
  `test_selo_verdadeiro_do_fato_ou_fake_baixa`, que trava o comportamento errado. Esperado: 3→2 graves.
- [ ] **B2. Regra de combinação entre afirmações** (`decisao.py:281-285`) — erra nas DUAS direções:
  (a) afirmação A com L=+2 e B com L=−5 → `alta` (falso positivo; achado do review externo);
  (b) parte falsa abaixo de τ (ENGANOSO sozinho = +1,05) deixa a parte verdadeira decidir → `baixa`
  (caso `cr_lula_acabar_bets_que_criou`). Nenhum teste cobre (mutação sobrevive). Projete uma regra que acerte os
  dois exemplos, escreva os dois testes, e só então implemente. Considere julgar também a afirmação original inteira.
- [ ] **B3. Rede social julgada só pelo título** (R2b): todas as SUSTENTA de rede social numa afirmação = 1 cluster.
  Causa do único erro grave real (`cr_chico_cesar_proibiu_direita`). **Não** use "rede social não vota" (cria grave
  novo). A variante "rede social não pode SUSTENTA" (assimétrica) exige decisão do usuário (§5).
- [ ] **B4. Agência sozinha nunca chega a alta** (R1+R11): REFUTA de agência curada vale como selo; com agência
  refutando, SUSTENTA de não-agência não vota. 5 casos tinham agência refutando e ficaram em `média`.
- [ ] **B5. Trava VAGO** só para sujeito genérico (economia, país, governo); hoje dispara em
  "Ibuprofeno piora o quadro de dengue".
- [ ] **B6. Independência** (`corroboracao.py:122,131,198-205`, `catalogo.py:183`): assinatura de agência não pode
  casar crédito de foto ("Folhapress"); aliases do mesmo portal (Lupa em 2 domínios) = mesmo cluster;
  "curada" por host exato ou prefixo declarado (hoje `comentarios1.folha.uol.com.br` vale como Folha).
- [ ] **Anti-overfitting**: as regras B3/B4 foram desenhadas olhando o dev. Não existem traces do holdout.
  Validar no holdout exige **nível 4 parcial** (record do holdout, ~25 casos): peça aprovação antes.
  Commit + PR "Fase B" com a tabela antes/depois (dev) e a pendência do holdout explícita.

### Fase C — correções de código (nível 1; replay)

- [ ] **C1.** Moldura "É falso que…" aplicada por frase, não sempre ao item 0 (`afirmacoes.py:184-193`).
- [ ] **C2.** Corte único do trecho preservando a conclusão da checagem (hoje `aprofundar` corta em 3000 e o juiz
  recorta em 2500 por sobreposição de termos; a frase "Conclusão: … é FALSO" some).
- [ ] **C3.** Juiz: classe fora do enum não invalida o lote inteiro (tratar item a item + `telemetria.fallback`).
- [ ] **C4.** deepseek esgota `max_tokens` em raciocínio (`finish_reason=length`, conteúdo vazio): aplicar
  `reasoning.effort` também ao juiz / subir `max_tokens` do juiz; não gravar 200 vazio em cassete; o retry de
  "resposta vazia" não deve repetir o mesmo payload.
- [ ] **C5.** Timeouts: juiz 45–60 s por chamada (hoje `LLM_TIMEOUT_S=240` sobrepõe tudo), `JUIZ_TIMEOUT_TOTAL_S`
  menor que o timeout do caso, usar `JUIZ_CONCORRENCIA` (hoje ignorado; juiz = 38% da latência); SerpAPI 10 s + 1 retry.
- [ ] **C6.** Decodificação: respeitar `content-type`/charset (páginas latin-1 perdem acentos); PDFs não vão ao
  trafilatura (0/66 extraídos) — pular ou extrair texto de PDF.
- [ ] **C7.** Crítico usa o mesmo critério do `decidir()` para parar (hoje 32% das paradas terminam em `média`).
- [ ] **C8.** Limpeza: docstrings/TODOs obsoletos (`cli.py:51-57`, `aprofundar.py:14-15`, `descoberta_site.py:13`);
  `Pipeline.executar` relança exceção — documentar ou devolver relatório `indeterminada` na CLI.
  Commit + PR "Fase C".

### Fase D — busca (nível 3; **pedir aprovação e informar custo antes**)

Maior impacto esperado em acerto (a checagem-gabarito só apareceu em 4/70 buscas). Medir na subamostra fixa de 20.

- [ ] **D1.** Trocar o `site:` com 13 agências em OR (3/377 resultados do tema) por `site:` de um domínio por vez
  nas 3–4 agências mais relevantes, ou busca interna dos sites; filtrar homes/listagens antes do juiz.
- [ ] **D2.** Manter o autor da fala ("Fulano disse X" é o que se checa) — regra 3 do prompt em `afirmacoes.py:144`
  apaga o autor (10 casos; em 3 a negação da fala virou negação do usuário).
- [ ] **D3.** Não crawlear Instagram/Facebook/TikTok (0 corpos em 264) nem PDFs; seguir o link da checagem citada
  em páginas de listagem das agências (75 listagens chegaram ao juiz).
- [ ] **D4.** Reformulação sem "checagem/é falso" e sem inventar entidades.
- [ ] **D5.** H1: veredito pelo título dos resultados de agências curadas (`#FAKE`, "É falso que…") via
  `selos`/`ingestor` — só depois de D1.

## 5. Decisões que são do usuário (pergunte; não decida sozinho)

1. Política de `sem_evidencia`: `alta` para "boato sem prova" é erro ou acerto? Separar em "vago" × "boato sem prova"?
2. Corrigir os rótulos de `cr_ibge_trafico_no_pib` e `cr_jn_soltura_vorcaro` (reescrita mudou o fato; evidência em
   `review2/r_eval.md`) — com justificativa na `nota`, nunca em silêncio.
3. Regra editorial assimétrica "rede social não pode confirmar".
4. Incluir AFP Checamos no catálogo como agência curada.
5. Revisar `factcheck_mvp/data/selos.json` (`revisao_humana: pendente`; 63/77 rótulos do eval dependem dela) e
   os 16 portais `descoberta-automatica` do `catalogo.json` (remover `coracaoevida-com-br`, que entrou por engano;
   `valorinveste-checagem` e `rede-saude-fiocruz` ainda têm `tipo: checagem`).
6. Ampliar o dataset: ≥30 verdadeiros no dev (hoje 8), casos `link`, e os 21 casos propostos em `review2/r_eval.md`.

## 6. Critério de pronto de cada fase

`pytest` verde · métricas novas (A1) no relatório com as linhas triviais · delta registrado em `eval/ITERACOES.md`
(hipótese, mudança, métricas antes/depois, run_ids) · nenhum erro grave novo · commit por tema + PR ·
o que ficou sem validar (holdout) escrito explicitamente no PR.
