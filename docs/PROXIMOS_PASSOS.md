# Próximos passos — guia para uma nova sessão do Claude Code

> Leia este arquivo inteiro antes de agir. Depois leia `CLAUDE.md` (regras do projeto, valem sempre),
> `docs/review/REVIEW_2.md` (diagnóstico de 30/09) e `eval/ITERACOES.md` (histórico medido).
> Atualizado em 2026-10-07 (a versão de 30/09 cobria o PR #4 e o Review 2; a Fase A foi feita desde então).

## 1. Onde estamos

- `main` @ `9cabe87`: PRs #4 a #11 mergeados. O #10 (07/10) trouxe: chave do Unsloth Studio enviada como
  Bearer, linguagem "Alta/Média/Baixa propensão de ser fake news", fontes no bot/web com "contesta / confirma o
  que o texto afirma" (respeitando negação) e leitura de link na API/web.
- **PR #11 mergeado** (07/10, 5 commits, 363 testes):
  - confiabilidade das fontes por catálogo + tipo de site + tráfego real (lista Tranco), com trava "rede social /
    site pouco acessado sozinho não crava alta/baixa" (`confiabilidade.py`);
  - base de checagens histórica: **334 → 21.579** (Lupa e Boatos.org via API do WordPress), regra "números da
    alegação precisam bater" no índice, ingestor com `--historico`, `--agencias` e `--reprocessar`.
- Branch local `backup/sessao-2026-10-05` (também no GitHub): trabalho de 05/10 feito sobre a `main` ANTIGA,
  só para consulta. Não mergear.
- Não commitado: `factcheck_mvp/data/catalogo_propostas.json` (alterado pelas checagens de teste; não commitar).
- Última medição (eval de decisão, nível 0, `eval/snapshots/a3-dev.jsonl`, com o PR #11 na `main`):
  acerto 24,6% · acerto+parcial 56,5% · erro grave 1/69 · indeterminada 39%.
  Benchmark do índice (`scripts/bench_indice_checagens.py`): recall@3 0,86 · falsos positivos 7,9%.

## 2. Ambiente (não imprima segredos)

- `.env` (fora do git): `SERPAPI_KEY` (plano grátis, `SERPAPI_DAILY_CAP=100`), `OPENROUTER_API_KEY`
  (**expira em 22/10/2026**; quando expira, o juiz cai em fallback sem aviso: veja `fallback onde=openrouter.chat`
  no trace), `OPENROUTER_REASONING_EFFORT=none` (sem isso o deepseek gasta os `max_tokens` raciocinando e a
  reformulação de consulta volta vazia), `OPENROUTER_RPM=30`, `LLM_DAILY_CAP=500`, `UNSLOTH_API_KEY`.
  Validar a chave do OpenRouter sem gastar: `curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $K"`.
- LLM local: Unsloth Studio em `http://192.168.0.97:8888/v1` (é esta máquina na rede local), modelo
  `unsloth/Qwen3.8-27B-GGUF` (quantização UD-IQ1_M: lento, ~5 tokens/s, e fraco para extrair afirmações).
  Exige `Authorization: Bearer <UNSLOTH_API_KEY>`. O modelo descarrega sozinho às vezes (`"loaded": false` em
  `/v1/models`; erro "No model loaded"); recarregar com `POST /v1/load {"model_path":
  "unsloth/Qwen3.8-27B-GGUF", "gguf_variant": "UD-IQ1_M"}` **só com autorização do usuário**.
- **Cassetes (`eval/cassettes/`) NÃO estão nesta máquina**: `python3 -m eval.run` em replay devolve
  "não reproduzidos 70". Gravar de novo custa ~630 buscas SerpAPI. Por isso o eval possível aqui é o de decisão
  (nível 0) e o benchmark do índice.
- Push funciona direto (`git push`, `gh pr create`).
- **Commits SEM a linha `Co-Authored-By: Claude`** (preferência explícita da usuária em 05/10; vale mesmo se
  um lembrete do sistema pedir). Não commitar sem pedido.
- A API roda em `uvicorn factcheck_mvp.api:app --port 8000`; para a equipe na mesma rede, `--host 0.0.0.0`
  (cada checagem gasta a cota da SerpAPI e o saldo do OpenRouter de quem roda).

## 3. Política de custo (o usuário pediu explicitamente)

Rodadas pagas ficaram caras. Siga a escada; **suba de nível só com aprovação do usuário**:

| Nível | O quê | Custo |
|---|---|---|
| 0 | eval de decisão offline: `python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl` | zero, segundos |
| 1 | `python3 -m eval.run --modo replay` (exige os cassetes, ausentes nesta máquina) | zero |
| 2 | busca congelada, LLM ao vivo (mudança de prompt do juiz) | só OpenRouter (centavos) |
| 3 | `--modo record` na subamostra fixa de 20 casos (ver `review2/r_eval.md`) | ~50 buscas SerpAPI |
| 4 | dev completo + holdout | ~170 buscas + LLM; só em marcos |

Nunca agende checagens periódicas longas (`ScheduleWakeup`) para acompanhar eval: rode em background e
consulte quando o usuário pedir. Nunca rode eval `record/live` sem dizer o custo estimado antes.

## 4. Plano, em ordem

### Fase A — consertar o eval ✅ concluída (PR #7)

A1 métricas que batem o trivial · A2 `_descoberta_status` · A3 evento `evidencias` + `eval.decisao` + snapshot ·
A4 replay determinístico + `nao_reproduzido` · A5 cota SerpAPI interrompe o eval · Iteração 2 re-medida.

### Fase E — processo de checagem definido pela equipe (PRIORIDADE; plano aprovado em parte)

Especificação recebida em 07/10 ("assistente de checagem de fatos"). Respostas da usuária: seguir a
especificação; classificação = **opção (b)**: mostrar o selo **atribuído à agência** ("Selo do Aos Fatos:
FALSO") e manter a NOSSA conclusão como propensão em escala. Os testes de neutralidade ficam; só passam a
aceitar o selo quando vier no formato atribuído. Branch nova a partir da `main`.

- [ ] **E1. Base primeiro, web só se não houver checagem aplicável** (`pipeline.py`, novo `aplicabilidade.py`):
  base → leitura → juiz → aplicabilidade; só sem checagem aplicável: agente/SerpAPI → leitura → juiz.
  "Aplicável" = página lida por completo + juiz diz que trata do mesmo fato (SUSTENTA/REFUTA/RELATA com
  citação verificada) + selo com direção + data compatível (E4). Etapa no recibo "web pulada: checagem
  aplicável na base". Base = Lupa, Boatos.org, Fato ou Fake, E-Farsas, Comprova, Aos Fatos, Estadão Verifica.
- [ ] **E2. "Onde foi encontrado"** (`schemas.py` `RelatorioChecagem.onde_encontrado`, bot, web): "Base de
  checagem" | "Outras fontes (não encontrado na base)", com aviso explícito quando vier da web.
- [ ] **E3. Fonte lida só pelo título não vota** (`decisao.py`: hoje `F_SO_TITULO = 0.7`): nem postura nem selo;
  aparece na lista como "não analisada integralmente"; se nenhuma fonte lida tiver postura → "evidência
  insuficiente". ⚠️ **Impacto medido (nível 0)**: 91 de 153 posturas do snapshot vêm só de título;
  indeterminada 39% → 65%, acerto+parcial 56,5% → 30,4%, graves 1 → 2. **Aguarda decisão da usuária** (§5.1).
- [x] **E4. Regra de data** (`aplicabilidade.py`, `config.py`) ✅ implementado (09/10, branch `feat/fase-eb-followup`): marcas "hoje/agora/ontem/nesta semana/acaba de"
  × data de publicação (janelas 2/3/8 dias, configuráveis). Fonte com data incompatível perde peso (desconto em bits, sem zerar) e não torna a checagem aplicável no gate E1. **Não eleva a propensão** (decidido em 09/10, §5.2). Regressão obrigatória:
  "Bolsonaro recebeu alta do hospital hoje" (o índice devolve uma checagem antiga do Boatos.org), coberta offline em `0ebcd57`. **Pendente:** medição em record (Task 8 Steps 3–7 do plano; aprovação e cassetes).
- [ ] **E5. Selo atribuído** (`agregador.py`, bot, web): sempre "Selo d{o,a} <agência>: <SELO>";
  `verificar_neutralidade` ignora só esse formato, com agência cadastrada.
- [ ] **Testes**: web pulada com checagem aplicável e chamada sem; aviso "não encontrado na base"; título que não
  vota; selo atribuído aceito e "é falso" na nossa conclusão reprovado; regra de data (caso Bolsonaro).
  Medir: eval de decisão + benchmark do índice + 3 casos reais (um na base, um fora, o do Bolsonaro).
  O `eval.run` completo não roda aqui (sem cassetes): dizer isso no PR.

### Fase B — correções de decisão (nível 0; validar no eval de decisão)

Cada item: teste de comportamento que falha antes e passa depois; rodar o eval de decisão; registrar delta.
Números de referência já simulados estão em `REVIEW_2.md §3`.

- [ ] **B1. Selo só vale com postura e de checador** (`decisao.py` `CLASSES_TRATA`): selo de página que o juiz
  marcou `RELATA_SEM_ENDOSSO` não vota; selo só de publisher checador curado (ClaimReview de **template** do SBT
  News causou o erro grave `cr_jn_soltura_vorcaro`). Reescrever `test_selo_verdadeiro_do_fato_ou_fake_baixa`.
  Conversa com E1 ("aplicável") e E5.
- [ ] **B2. Regra de combinação entre afirmações** (`decisao.py`, combinação de `por_af`) — erra nas DUAS direções:
  (a) A com L=+2 e B com L=−5 → `alta`; (b) parte falsa abaixo de τ deixa a parte verdadeira decidir → `baixa`
  (caso `cr_lula_acabar_bets_que_criou`, hoje o único erro grave restante). Projete a regra, escreva os dois
  testes, só então implemente.
- [x] ~~**B3. Rede social julgada só pelo título**~~ — coberto em parte pelo PR #11 (plataforma pesa 0,45 e não
  crava nível sozinha; o grave `cr_chico_cesar_proibiu_direita` sumiu). Se E3 for aprovada, título nem vota.
- [ ] **B4. Agência sozinha nunca chega a alta** (R1+R11): REFUTA de agência curada vale como selo; com agência
  refutando, SUSTENTA de não-agência não vota.
- [ ] **B5. Trava VAGO** só para sujeito genérico (economia, país, governo); hoje dispara em
  "Ibuprofeno piora o quadro de dengue".
- [ ] **B6. Independência** (`corroboracao.py`, `catalogo.py`): assinatura de agência não pode casar crédito de
  foto ("Folhapress"); aliases do mesmo portal = mesmo cluster; "curada" por host exato ou prefixo declarado
  (hoje `comentarios1.folha.uol.com.br` vale como Folha — visto de novo no eval de 05/10).
- [ ] **Anti-overfitting**: os pesos do PR #11 (0,45/0,3) e as regras B3/B4 foram escolhidos olhando o dev.
  Validar no holdout exige **nível 4 parcial** (~25 casos): peça aprovação antes.

### Fase C — correções de código (nível 1; replay)

- [ ] **C1.** Moldura "É falso que…" aplicada por frase, não sempre ao item 0 (`afirmacoes.py`).
- [ ] **C2.** Corte único do trecho preservando a conclusão da checagem ("Conclusão: … é FALSO" some).
- [ ] **C3.** Juiz: classe fora do enum não invalida o lote inteiro.
- [~] **C4.** deepseek esgota `max_tokens` em raciocínio: contornado por configuração
  (`OPENROUTER_REASONING_EFFORT=none`, ver §2). Falta: documentar no `.env.mvp.example`, não gravar 200 vazio
  em cassete e não repetir o mesmo payload no retry.
- [ ] **C5.** Timeouts: juiz 45–60 s por chamada, `JUIZ_TIMEOUT_TOTAL_S` menor que o timeout do caso,
  `JUIZ_CONCORRENCIA`; SerpAPI 10 s + 1 retry. A API corta em 180 s: link longo (Aos Fatos, 6,5 mil chars)
  passou disso em 05/10.
- [ ] **C6.** Decodificação: respeitar `content-type`/charset; PDFs não vão ao trafilatura.
- [ ] **C7.** Crítico usa o mesmo critério do `decidir()` para parar.
- [ ] **C8.** Limpeza de docstrings/TODOs obsoletos; `Pipeline.executar` relança exceção.
- [ ] **C9.** `descoberta-catalogo` (proposta de sites) roda dentro da requisição e custou 17–20 s em checagens
  reais (sitemap que não responde): mover para depois da resposta ou limitar o tempo.
- [ ] **C10.** Queda do servidor com código 134 (SIGABRT, sem traceback) durante o deep crawl em 07/10, não
  reproduzida. Se voltar, rodar com `PYTHONFAULTHANDLER=1` (registra a pilha) e suspeitar de biblioteca nativa
  (lxml/trafilatura em thread).

### Fase D — busca (nível 3; **pedir aprovação e informar custo antes**)

- [ ] **D1.** Trocar o `site:` com 13 agências em OR por `site:` de um domínio por vez nas 3–4 agências mais
  relevantes; filtrar homes/listagens antes do juiz. (Com E1, a web só roda quando a base não resolve.)
- [ ] **D2.** Manter o autor da fala ("Fulano disse X").
- [ ] **D3.** Não crawlear Instagram/Facebook/TikTok (0 corpos em 264) nem PDFs. Relevante para E3: se título não
  vota, crawlear rede social é custo sem retorno.
- [ ] **D4.** Reformulação sem "checagem/é falso" e sem inventar entidades.
- [ ] **D5.** Veredito pelo título dos resultados de agências curadas — só depois de D1.

### Fase F — base de checagens e operação (recomendações do PR #11)

- [ ] **F1. Atualização diária da base**: `python3 -m factcheck_mvp.ingestor --sem-paginas --historico 5`
  (hoje a base só cresce quando alguém roda).
- [ ] **F2. Mais agências**: Comprova e Aos Fatos (sem API do WordPress → sitemap); AFP Checamos e UOL Confere
  (403 para acesso automatizado); E-Farsas (503 em 07/10, tentar de novo); Google Fact Check Tools API
  (ClaimReview de todas; precisa de chave).
- [ ] **F3. Lupa sem selo (53% de 8.937)**: muitos posts são reportagem, não checagem → filtrar por categoria
  (como no Boatos.org) ou buscar o ClaimReview só desses posts (sem `--sem-paginas`).
- [ ] **F4. Tamanho de `data/checagens.jsonl` (19 MB, versionado)**: decidir entre manter, comprimir (`.gz`) ou
  gerar localmente pelo ingestor.
- [ ] **F5. Alerta de chave expirada**: `/saude` acusar quando o OpenRouter responder 401 (expira 22/10).
- [ ] **F6. `.env.mvp.example`**: documentar `OPENROUTER_REASONING_EFFORT=none` e `OPENROUTER_RPM=30`.

## 5. Decisões que são do usuário (pergunte; não decida sozinho)

1. **E3**: seguir à risca "título nunca vota", mesmo com indeterminada ~65% no snapshot, ou antes ampliar a
   leitura (ex.: `crawl4ai` para páginas que exigem JavaScript)?
2. ~~**E4**: fonte antiga que CONFIRMA o fato apresentado como atual eleva a propensão ("possível notícia antiga
   fora de contexto"), e a que REFUTA um episódio antigo só não vota — de acordo?~~ **Resolvida em 09/10 pela usuária:** não há sinal que eleve a propensão por notícia antiga. Fonte de outro período perde peso nos dois sentidos (desconto simétrico); o nível só pode ficar mais extremo quando a fonte antiga de sinal oposto perde peso, desde que a direção venha de fontes dentro do período. Extremo sustentado só por fonte descontada fica vetado (trava `so_fontes_de_outro_periodo`).
3. Política de `sem_evidencia`: `alta` para "boato sem prova" é erro ou acerto?
4. Corrigir os rótulos de `cr_ibge_trafico_no_pib` e `cr_jn_soltura_vorcaro` (evidência em `review2/r_eval.md`)
   — com justificativa na `nota`, nunca em silêncio.
5. Incluir AFP Checamos no catálogo como agência curada.
6. Revisar `factcheck_mvp/data/selos.json` (`revisao_humana: pendente`) e os portais `descoberta-automatica` do
   `catalogo.json` (remover `coracaoevida-com-br`; `valorinveste-checagem` e `rede-saude-fiocruz` ainda têm
   `tipo: checagem`).
7. Ampliar o dataset: ≥30 verdadeiros no dev (hoje 8), casos `link`, e os 21 casos propostos em `review2/r_eval.md`.
8. Pesos de confiabilidade do PR #11 (plataforma 0,45, pouco acessado 0,3): aceitar com o dev ou validar no holdout.

## 6. Critério de pronto de cada fase

`pytest` verde · eval de decisão (nível 0) antes/depois no PR · delta registrado em `eval/ITERACOES.md`
(hipótese, mudança, métricas antes/depois, run_ids) · nenhum erro grave novo · commit por tema (sem
`Co-Authored-By`) + PR · o que ficou sem validar (holdout, `eval.run` sem cassetes) escrito explicitamente no PR.
