# E4 Relevância Temporal — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fonte publicada fora da janela do "hoje/ontem/nesta semana…" do texto perde peso de evidência
(mistura de razões de verossimilhança), sem nunca elevar a propensão por si; o desconto é medido em bits,
determinístico no eval e visível ao usuário como aviso neutro.

**Architecture:** `aplicabilidade.py` (puro) mede a janela do texto e normaliza datas; `decisao.decidir` (puro)
desconta cada contribuição com `r = relevancia_temporal(e, janela)` → `sinal·ln(r·e^|c| + 1 − r)`;
o pipeline resolve a **data de referência** (entrada explícita → relógio gravado via `replay.*` → nenhuma) e a
**data da fonte** normalizada; o gate do E1 continua binário, alinhado às mesmas entradas.

**Tech Stack:** Python 3 (`python3`), pytest, dataclasses/Pydantic v2, cassetes `replay.*`, telemetria
`runs/<id>/trace.jsonl`, eval nível 0 (`python3 -m eval.decisao`).

**Spec:** `docs/PROXIMOS_PASSOS.md` §4 E4 + §5.2, **substituído** pelas decisões da usuária de 09/10:
E4 **não eleva** a propensão; desconto informacional simétrico; bits descartados reportados
(`Decisao.descontos_temporais`, `travas['data_incompativel']`); motivo "podem tratar de outro episódio —
verifique se não é notícia antiga recirculando" quando o nível fica `media` por isso. E3 aprovado e já no código.

## Estado de implementação (09/10, fim do dia — branch `feat/e4-relevancia-temporal`)

| Task | Estado | Evidência |
|---|---|---|
| 0 | ✅ feita | hiperbólica no HEAD + `test_e4_relevancia_temporal_contrato` verde |
| 1 | ✅ implementada + review Approved | 4 testes novos; review sem Critical/Important (só minors diferidos) |
| 2 | ✅ implementada (review pendente) | 17 casos `normalizar_data` + prioridade da data; cobertura 90,7% (era ~20%) |
| 3 | ✅ implementada (review pendente) | relógio `replay.hoje` (live/record/replay-miss→None+fallback); 40+10 testes |
| 4 | ✅ implementada (review pendente) | exclusões/extensões + `janela_da_afirmacao`; check eval: só `cr_el_nino` 2→32 |
| 4b | ✅ implementada (review pendente) | `marco_do_evento` + `marco=(D,folga)`; 5 testes TDD, frases fora do eval |
| 5 | ⬜ não iniciada | gate E1 ainda usa janela/referência/data antigas |
| 6 | ✅ implementada (review pendente) | aviso neutro em `justificativa`/`why_1linha` + 📅 por fonte; `verificar_neutralidade == []` |
| 7 | ✅ implementada (review pendente) | `fonte estagio=data`, `resumo_trace`, `--sem-e4`, `--gerar-snapshot`, `metricas.e4` |
| 8 | ⬜ não iniciada | sem regressão Bolsonaro REFUTA→"não alta"; sem record (precisa aprovação); `ITERACOES.md`/`PROXIMOS_PASSOS.md` intactos |

- Suite no commit: **467 passed, 0 failed** (`python3 -m pytest tests -q`).
- Incidentes revertidos (comportamento fora deste plano, introduzido 2x, revertido 2x — R6 no ledger):
  peso `f_fake`, filtro "selo de página não vota", testes `test_t6_*`, reescrita do teste pinado do selo.
  Árvore limpa confirmada por grep no commit.
- Flaky conhecido (ambiental, sem relação com E4): `test_orcamento_parcial_preservado`
  (budget 0,15s; contagem 4-vs-5 eventos; passa intermitente).
- Pendente após o commit: reviews das Tasks 4b/6/7, Task 5, Task 8, final whole-branch review.

## Global Constraints

- `python3 -m pytest tests -q` verde antes de fechar cada task (09/10: **387 passed** com a Task 0 feita).
- **`relevancia_temporal` já tem curva decidida pela usuária (hiperbólica, 09/10)**. Nenhuma task deste plano
  altera a curva; testes de comportamento novos fazem `monkeypatch` da função com um stub
  (`lambda e, j: 0.05`), para não depender da curva escolhida.
- Nunca editar casos, rótulos, `esperado` ou `eval/baseline.json` para passar. Campo opcional novo em caso
  (`data_referencia`) só com justificativa na `nota` + registro em `eval/ITERACOES.md`, decidido **antes** de rodar.
- Não ajustar janelas/curva para acertar um caso do eval; marcadores novos precisam de razão linguística geral e
  teste próprio (frases fora do eval).
- Todo fallback novo → `telemetria.fallback(onde, motivo)`; todo I/O novo (inclusive relógio) → `replay.*`.
- Sem rede live; **sem eval `--modo record`** neste plano sem aprovação explícita (Task 8 só *pede*; custo
  estimado ~50 buscas SerpAPI, nível 3 da escada de custo em `PROXIMOS_PASSOS.md` §3).
- Não commitar sem pedido; se pedido, commits por tema **sem** `Co-Authored-By` (preferência da usuária, §2).
- Não ler `bot.log`; nunca imprimir chaves do `.env`.

## Estado de partida (09/10, não commitado — `git diff`)

| Arquivo | O que já existe |
|---|---|
| `factcheck_mvp/aplicabilidade.py:17-25` | `_JANELAS` (hoje/agora/acaba de/acabou de=2, ontem=3, nesta/neste semana=8) |
| `aplicabilidade.py:38-45` | `janela_temporal(texto) -> int \| None` (menor janela entre os marcadores) |
| `aplicabilidade.py:48-65` | `dias_excedentes(texto, data_pub, referencia=None) -> (janela, excedente) \| None`; `referencia=None` ⇒ **data atual UTC** (`:62`) |
| `aplicabilidade.py:68-71,91` | `data_compativel` binária (excedente == 0), usada pelo gate `e_aplicavel` (E1) **sem referência** (agora UTC) |
| `factcheck_mvp/decisao.py:224-232` | `relevancia_temporal(excedente, janela)` → `raise NotImplementedError` (TODO da usuária) |
| `decisao.py:235-244` | `_descontar(valor, r)` = mistura simetrizada |
| `decisao.py:324-336` | laço de desconto por item em `decidir`, `descontos_temporais` |
| `decisao.py:400-404` | motivo "notícia antiga recirculando" se `media` e houve desconto; `travas["data_incompativel"]` |
| `factcheck_mvp/pipeline.py:511,516-517` | `ItemEvidencia.data_pub`, `texto_usuario=texto_base`, `data_referencia=` **data atual UTC** |
| `eval/decisao.py` | snapshot lê `data_pub`, `texto_usuario`, `data_referencia` (opcionais) |
| `tests/test_decisao.py` | 5 testes E4 (contrato de `r`, Bolsonaro SUSTENTA ≠ baixa, simetria, sem desconto, mistura) |

Nível 0 atual (E3 já ativo; E4 é no-op neste snapshot — ver Achado 7):
`python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl` → **acerto 15,9% · acerto+parcial 30,4% ·
erro_grave 2/69 · indeterminada 65,2%** (níveis: indeterminada 45, media 14, alta 6, baixa 4).

## Achados da investigação (com números medidos)

**A1. `data_pub` — de onde vem, por caminho.**

| Caminho | Origem no código | Formato real |
|---|---|---|
| Base (índice) | `pipeline.py:316` ← `checagens.jsonl` via `ingestor._data_iso` (`ingestor.py:227-241`, converte para **UTC**) | 21.579/21.579 preenchidas; 21.560 `2026-09-22T14:03:44+00:00`, 19 `YYYY-MM-DD` |
| Web (SerpAPI) | `serpapi_layer.py:428` (`google`: `item["date"]`), `:436` (`iso_date`, news), `:417` (Scholar: só o **ano**) → `pipeline.py:240` | cassetes: orgânicos 61% sem data, 33% `"26 de jan. de 2012"`, 6% relativa `"há 2 dias"`; **nenhum ISO** |
| Deep crawl | `extracao.py:428` JSON-LD `datePublished` → `:445` trafilatura `date` → `:480` ReaderLM "as written" | ISO com fuso / `YYYY-MM-DD` / texto livre |
| Junção | `pipeline.py:628-629`: data da página **só entra se a peça não tem data** | ⚠️ a string da SerpAPI (texto PT-BR) **bloqueia** a data ISO da página |

Medição em 596 relatórios (`runs/*/resultado.json`, 5.612 fontes): parseáveis hoje por `data_pub[:10]` só
**~20%** (`YYYY-MM-DD` 15,9% + ISO 3,6%); `"11 de ago. de 2025"` **38,3%** (1.850 delas com corpo lido — a página
tinha data melhor); relativas (`há 3 dias`, `2 dias atrás`) 5,1%; só ano 0,9%; vazias 36,2%.
Datas suspeitas: 138 de 890 `YYYY-MM-DD` são **1º de janeiro** (`2000-01-01` ×47 em gov.br/translate, `2026-01-01`
×30 em agencialupa.org) — padrão de "só ano"/placeholder do extrator. Consequência: sem normalização o E4 quase
nunca dispara na web e, quando dispara com `-01-01`, superestima o excedente.

**A2. Fuso.** Referência e base estão em UTC (`pipeline.py:517`, `ingestor.py:232`); o usuário está em
UTC−3. Texto com "hoje" enviado às 22h BRT já é "amanhã" em UTC; peça publicada 23h BRT vira dia seguinte.
Erro de 1 dia numa janela de 2 — relevante na borda. O Brasil não tem horário de verão desde 2019: UTC−3 fixo é
exato e dispensa `zoneinfo`.

**A3. Marcadores (`_JANELAS`).** Nos 95 casos do eval só **3** têm marcador: `cr_hytalo_santos_morreu` (dev,
"acabou de sair a notícia"), `cr_el_nino_catastrofe_setembro` (dev, "**agora em setembro**" — falso positivo:
refere-se ao mês), `cr_video_lula_ministros_stf_atual` (holdout, "agora, no meio da crise"). Falsos positivos
gerais não tratados: "hoje em dia", "até hoje", "até agora", "a partir de agora", "agora em <mês>". Faltam
marcadores gerais: "esta manhã/tarde/noite", "neste domingo/nesta segunda(-feira)…", "anteontem", "há pouco/há
instantes/há poucas horas", "neste fim de semana", "semana passada", "na última <dia>". Datas explícitas
("dia 8", "8 de outubro", "08/10") exigem âncora diferente (data do evento, não "hoje") — ver Task 4b.
**Escopo:** o marcador do texto inteiro vale hoje para *todas* as afirmações (`decisao.py:325` usa
`ev.texto_usuario`), enquanto o gate do E1 usa `afs[ai].texto` (`pipeline.py:554-556`), que o extrator pode
reescrever sem o "hoje". As duas pontas medem coisas diferentes.

**A4. Determinismo.** `data_referencia` = data atual (`pipeline.py:517`): replay de outro dia muda o excedente
e o nível de casos com marcador. Cassetes guardam `gravado_em` (`replay.py:271`) e o JSON da SerpAPI traz
`search_metadata.created_at` (266/266 cassetes SerpAPI) — âncora determinística para datas relativas
("há 2 dias"). Casos do `casos_claimreview.jsonl` têm `data_checagem` (ex.: Hytalo `2026-09-27`). O evento
`evidencias` já serializa `data_referencia`/`texto_usuario`/`data_pub` (`asdict(ev)`), então snapshots novos de
nível 0 saem determinísticos por construção.

**A5. Entrada por link.** `api.py:121` / `telegram_bot.py:312` concatenam o texto da página lida à entrada: um
"hoje" dentro de uma matéria de 2021 seria medido contra a data atual. Para link, a referência certa é a data da
própria página; sem ela, E4 deve ficar desligado.

**A6. Defeitos no código E4 já escrito.**
1. `decisao.py:333`: `direcao` é calculada **depois** do desconto (`contribs[0][0] > 0`); com `r = 0` o valor vira
   0 e a direção sai −1 errada.
2. `decisao.py:334`: `bits_descartados` soma postura **e** selo do mesmo item, mas o cluster só usa o maior
   (`max |c|`, `decisao.py:345`): superestima os bits.
3. Com `r = 0` (curva com corte duro) todas as contribuições zeram → sem voto → motivo cai em
   `decisao.py:388` "as fontes com postura se anulam…" — falso.
4. O motivo `media` (`decisao.py:400-403`) dispara se **houve** desconto, não se o desconto **mudou** o nível.
5. `justificativa()` (`decisao.py:154-183`) e `why_1linha()` (`:185-192`) só mostram `motivo` quando
   `indeterminada`: o aviso de `media` **nunca chega** ao bot/web hoje.
6. `resumo_trace()` (`:198-209`) não lista os descontos; `cli trace` só mostra o booleano das travas.

**A7. Medição nível 0.** `eval/snapshots/a3-dev.jsonl` tem 977 itens e **nenhum** `data_pub`, nenhum
`texto_usuario`: o E4 é no-op ali por construção (serve só de guarda de regressão). Medir o E4 exige snapshot
novo gerado de traces pós-E4 (Task 7/8). Na `eval/subamostra20.txt`, só `cr_hytalo_santos_morreu` tem marcador:
a subamostra mede "não piorou", não "melhorou".

**A8. Regressão Bolsonaro com a base real.** `Indice.de_checagens().buscar_checagens("Bolsonaro recebeu alta do
hospital")` devolve Boatos.org **FALSO** de `2026-01-14` ("…alta… em janeiro de 2026") e de `2025-04-23`. Sem E4,
esses selos REFUTAM e empurram para **alta**: se a alta de hoje for real, é **erro grave** (verdadeiro→alta).
O teste existente só cobre a direção SUSTENTA→"não baixa"; falta a direção REFUTA→"não alta".

## Review Focus

- E4 nunca eleva nem baixa o nível sozinho: `|L|` só diminui item a item; teste de simetria + teste de que o
  contrafactual sem desconto tem `|L|` ≥ com desconto.
- Datas: precisão conservadora (usar o **fim** do intervalo: "2021" → 2021-12-31; "há 3 dias" ancorado na data
  da busca), nunca descontar mais do que a data permite afirmar.
- Determinismo: replay de outro dia dá o mesmo nível; relógio ausente no cassete → `fallback onde=relogio` e E4
  desligado (nunca "data de hoje" silenciosa).
- Gate E1 e decisão usam a **mesma** janela, referência e data normalizada.
- Neutralidade: o aviso não contém nada de `agregador.EXPRESSOES_PROIBIDAS` ("notícia falsa", "é falso"…).

---

## File Structure

- **Modify `factcheck_mvp/aplicabilidade.py`** — marcadores (com exclusões), `normalizar_data`, `hoje_brt`,
  `janela_da_afirmacao`; `dias_excedentes(referencia=None)` passa a significar "referência desconhecida → não mede".
- **Modify `factcheck_mvp/decisao.py`** — correções A6, contrafactual `L_sem_desconto`, texto ao usuário,
  `resumo_trace`. **Não** tocar no corpo de `relevancia_temporal`.
- **Modify `factcheck_mvp/config.py`** — `E4_JANELA_HOJE=2`, `E4_JANELA_ONTEM=3`, `E4_JANELA_SEMANA=8`
  (+ as novas da Task 4).
- **Modify `factcheck_mvp/replay.py`** — `hoje(contexto) -> str | None` (relógio gravado em cassete).
- **Modify `factcheck_mvp/schemas.py`** — `EntradaConsulta.data_referencia: Optional[str]`;
  `FonteEvidencia.data_pub_bruta`, `relevancia_temporal`.
- **Modify `factcheck_mvp/serpapi_layer.py`** — `normalizar_item(..., ancora=None)` produz `data_publicacao_iso`.
- **Modify `factcheck_mvp/pipeline.py`** — resolve referência; normaliza datas; prioridade da data da página;
  gate alinhado; eventos `fonte estagio=data`; limitações/perguntas.
- **Modify `factcheck_mvp/agente.py:144`** — repassa a âncora para `normalizar_item`.
- **Modify `factcheck_mvp/telegram_bot.py`, `factcheck_mvp/api.py`** — linha de data por fonte; `data_referencia`
  de link/encaminhamento.
- **Modify `eval/run.py`, `eval/decisao.py`** — `data_referencia` opcional do caso; `--sem-e4`;
  `--gerar-snapshot --resultado <dir>`; bloco `e4` nas métricas.
- **Modify `docs/TELEMETRIA.md`, `eval/README.md`, `docs/PROXIMOS_PASSOS.md`** (E4 e §5.2), **`eval/ITERACOES.md`**.
- **Tests:** `tests/test_aplicabilidade.py`, `tests/test_decisao.py`, `tests/test_pipeline.py`,
  `tests/test_replay.py`, `tests/test_eval_harness.py`, `tests/test_telemetria.py`, `tests/test_bot_modelo.py`, `tests/test_api_link.py`, `tests/test_serpapi_layer.py`.

---

### Task 0 ✅ FEITA (09/10): `relevancia_temporal` hiperbólica — esforço P

**Decisão da usuária:** curva hiperbólica `r = janela / (janela + excedente)`. Escala relativa à janela
(um dia além de "nesta semana" pesa menos que um além de "hoje"); `r` nunca chega a 0 exato (cauda longa:
"hoje" + 10 dias → 0,17; + 365 dias → 0,005), então fonte de outro episódio sobra como evidência fraca
(`media`), não some. Testes de contrato verdes. O texto abaixo fica como registro do contrato.

**Files:** `factcheck_mvp/decisao.py:224-232` (só o corpo; assinatura e docstring ficam).

**Contrato (já pinado por `tests/test_decisao.py::test_e4_relevancia_temporal_contrato`):**
- `relevancia_temporal(excedente: int, janela: int) -> float`, puro, sem I/O.
- `r(0, j) == 1.0` para toda janela; `0 ≤ r ≤ 1`; **não crescente** em `excedente`; `r(2000, 2) < 0.05`.

**Decisões que a usuária toma ao escrever (o plano não escolhe):**
- Escala: `r` depende de `excedente` sozinho ou de `excedente/janela` (um dia além de "nesta semana" pesa
  menos que um dia além de "hoje"?).
- Cauda: `r` pode chegar a **0 exato**? Com 0 a fonte some (pode virar `indeterminada`; Task 1 trata o motivo);
  com `r > 0` sempre, sobra "evidência fraca" (`media`).
- Ordem de grandeza útil para conferir: com `W_VEREDITO = 1,5`, `r = 0,2` deixa 0,53 nats (descarta 1,4 bit);
  `r = 0,05` deixa 0,16 nats (1,9 bit); três clusters com `r = 0,05` somam 0,48 < τ = ln 3 → `media`.

- [x] **Step 1:** usuária escolheu a hiperbólica; corpo escrito em `decisao.py`.
- [ ] **Step 2:** `python3 -m pytest tests -q` → Expected: **0 falhas** (as 4 de hoje passam:
  3 em `test_decisao.py`, 1 `test_pipeline.py::test_checagem_antiga_para_fato_de_hoje_nao_pula_web`).
- [ ] **Step 3:** `python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl` → Expected: idêntico ao
  estado de partida (15,9% / 30,4% / 2 / 65,2%), porque o snapshot não tem datas (A7).

---

### Task 1: Correções do desconto em `decidir` + contrafactual — esforço M

**Files:**
- Modify: `factcheck_mvp/decisao.py:297-404` (laço de desconto, elegibilidade, motivo), `Decisao` (campo novo)
- Test: `tests/test_decisao.py`

**Interfaces:**
- Produces: `Decisao.log_odds_sem_desconto: float`, `Decisao.nivel_sem_desconto: str`;
  cada item de `descontos_temporais` = `{url, afirmacao, data_pub, janela, dias_alem_da_janela, r, direcao,
  nats_antes, nats_depois, bits_descartados}` com `direcao` do valor **antes** do desconto e bits sobre o maior
  `|c|` do item (o que o cluster de fato usa).

- [ ] **Step 1: Write the failing tests** (stub de `r`, independente da curva da usuária)

```python
def test_e4_direcao_e_bits_medidos_antes_do_desconto(monkeypatch):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.0)
    it = ItemEvidencia(url="https://g1.globo.com/a", cluster="g1", classe="REFUTA", motor=JUIZ,
                       citacao_verificada=True, curada=True, corpo_lido=True,
                       veredito="FALSO", origem_veredito="pagina", data_pub="2021-01-01")
    d = decidir(_ev_hoje([it]))
    x = d.descontos_temporais[0]
    assert x["direcao"] == 1
    assert x["bits_descartados"] == pytest.approx(decisao.W_VEREDITO / math.log(2), rel=1e-3)


def test_e4_tudo_descontado_motivo_fala_de_periodo(monkeypatch):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.0)
    itens = [ItemEvidencia(url=f"https://{d}/a", cluster=d, classe="REFUTA", motor=JUIZ,
                           citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2021-01-01")
             for d in ("g1.globo.com", "bbc.com")]
    d = decidir(_ev_hoje(itens))
    assert d.nivel == "indeterminada"
    assert "outro episódio" in d.motivo and "se anulam" not in d.motivo


def test_e4_motivo_so_quando_o_desconto_muda_o_nivel(monkeypatch):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    recente = [ItemEvidencia(url=f"https://{d}/a", cluster=d, classe="REFUTA", motor=JUIZ,
                             citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2026-10-08")
               for d in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    antiga = ItemEvidencia(url="https://uol.com.br/a", cluster="uol", classe="REFUTA", motor=JUIZ,
                           citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2021-01-01")
    d = decidir(_ev_hoje(recente + [antiga]))
    assert d.nivel == d.nivel_sem_desconto == "alta"
    assert d.travas["data_incompativel"] and "outro episódio" not in d.motivo


def test_e4_contrafactual_nunca_menor_em_modulo(monkeypatch):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.3)
    itens = [ItemEvidencia(url=f"https://{d}/a", cluster=d, classe=c, motor=JUIZ, citacao_verificada=True,
                           curada=True, corpo_lido=True, data_pub=dp)
             for d, c, dp in (("g1.globo.com", "REFUTA", "2021-01-01"), ("bbc.com", "REFUTA", "2026-10-09"))]
    d = decidir(_ev_hoje(itens))
    assert abs(d.log_odds) <= abs(d.log_odds_sem_desconto)
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_decisao.py -k "e4_" -v`
Expected: FAIL nos 4 novos (`KeyError`/`AttributeError: nivel_sem_desconto`, motivo errado).

- [ ] **Step 3: Implement**
1. No laço (`decisao.py:324-336`): guardar `brutos = contribs` antes do desconto; `direcao = sinal(max por |v|
   de brutos)`; `nats_antes = max|v| brutos`, `nats_depois = max|v| descontados`;
   `bits = (nats_antes − nats_depois)/ln 2`.
2. Acumular em paralelo `clusters_brutos` e calcular `L_sem_desconto` com **a mesma** regra de cluster, trava
   `sem_fonte_confiavel` e combinação entre afirmações (extrair a agregação de `L_a` para uma função interna
   `_agregar(clusters, a_idx, dec, registrar: bool)` para não duplicar regra; só a passada "com desconto"
   registra votos/travas).
3. Elegibilidade: se não há voto **e** `L_sem_desconto != 0` **e** houve desconto → motivo
   `"as fontes com posição são de antes do período que o texto descreve (\"hoje\", \"ontem\"…) e podem tratar
   de outro episódio — verifique se não é notícia antiga recirculando"` (antes do ramo "se anulam", `:388`).
4. Motivo `media` (`:400`): trocar a condição por `dec.nivel != dec.nivel_sem_desconto`.
5. `parametros["e4"] = {"janelas": <config>, "formula": "sinal·ln(r·e^|c| + 1 − r)"}`.

- [ ] **Step 4: Run** `python3 -m pytest tests/test_decisao.py -q` → Expected: PASS (os 5 E4 antigos + 4 novos).
- [ ] **Step 5: Commit (só se pedido)** `fix: E4 mede direção e bits antes do desconto e só avisa quando muda o nível`

---

### Task 2: Normalização de `data_pub` (pura) + prioridade de fontes de data — esforço M

**Files:**
- Modify: `factcheck_mvp/aplicabilidade.py` (nova `normalizar_data`), `factcheck_mvp/serpapi_layer.py:358-441`
  (`normalizar_item(..., ancora=None)`), `factcheck_mvp/pipeline.py:216,240,316,628-629`, `factcheck_mvp/agente.py:144`
- Test: `tests/test_aplicabilidade.py`, `tests/test_pipeline.py`

**Interfaces:**
- Produces: `normalizar_data(valor: str | None, ancora: str | None = None) -> tuple[str, str] | None`
  → `(data_fim_iso "YYYY-MM-DD" em UTC−3, precisao "dia"|"ano")`; `None` se vazio/ilegível.
- Peça ganha `data_pub` (normalizada, fim do intervalo), `data_pub_precisao`, `data_pub_bruta` (original, para
  exibir). `ItemEvidencia.data_pub` recebe a normalizada (sem campo novo no dataclass).

Regras (gerais, não por caso):
- ISO com fuso → converte para UTC−3 antes de pegar a data (A2); ISO sem fuso / `YYYY-MM-DD HH:MM:SS` → data.
- `YYYY-MM-DD` exatamente `-01-01` **sem hora** → precisão `ano` (fim = `YYYY-12-31`) — padrão de placeholder
  do extrator (A1). Sentinelas `1970-01-01`, `1900-01-01`, `2000-01-01` sem hora → `None`.
- PT-BR: `"26 de jan. de 2012"`, `"26 de janeiro de 2012"`, `"26/01/2012"` → dia.
- Relativas: `"há N minutos|horas|dias|semanas|meses|anos"`, `"N dias atrás"`, `"N days ago"` → exige `ancora`
  (sem âncora → `None`); meses/anos → precisão `ano`-like conservadora (fim = âncora − N·30/365 dias).
- Só ano (`"2021"`, Scholar `serpapi_layer.py:417`) → precisão `ano`.
- Futuro em relação à âncora → mantém (excedente sai 0 por `max(0, …)`).
- Não-vazio e ilegível → `None` + `telemetria.fallback("data_pub", "formato não reconhecido", valor=v[:40])`
  (chamado pelo pipeline, não pela função pura). Vazio não é fallback.

Prioridade no pipeline (substitui o "primeiro que chegar" de `pipeline.py:628`): JSON-LD da página (dia) >
SerpAPI absoluta/ISO (dia) > trafilatura (dia) > SerpAPI relativa ancorada > ano > ReaderLM; troca a data só se a
nova tem precisão maior ou a atual é `None`.

Âncora da SerpAPI: `payload["search_metadata"]["created_at"]` ("2026-09-28 20:40:30 UTC"), presente em 266/266
cassetes → determinístico em replay. `pipeline.py:216` e `agente.py:144` passam `ancora=` para `normalizar_item`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from factcheck_mvp.aplicabilidade import normalizar_data

@pytest.mark.parametrize("valor,ancora,esperado", [
    ("2026-09-22T02:00:00+00:00", None, ("2026-09-21", "dia")),   # 23h BRT do dia 21
    ("2024-02-23T16:09:57-03:00", None, ("2024-02-23", "dia")),
    ("2024-04-04 13:21:00", None, ("2024-04-04", "dia")),
    ("26 de jan. de 2012", None, ("2012-01-26", "dia")),
    ("3 de julho de 2026", None, ("2026-07-03", "dia")),
    ("08/10/2026", None, ("2026-10-08", "dia")),
    ("há 3 dias", "2026-10-09", ("2026-10-06", "dia")),
    ("2 dias atrás", "2026-10-09", ("2026-10-07", "dia")),
    ("há 5 horas", "2026-10-09", ("2026-10-09", "dia")),
    ("2021", None, ("2021-12-31", "ano")),
    ("2026-01-01", None, ("2026-12-31", "ano")),
])
def test_normalizar_data_formatos_reais(valor, ancora, esperado):
    assert normalizar_data(valor, ancora) == esperado

@pytest.mark.parametrize("valor", [None, "", "2000-01-01", "1970-01-01", "ontem à tarde", "há 3 dias"])
def test_normalizar_data_desconhecida_vira_none(valor):
    assert normalizar_data(valor, None) is None  # relativa sem âncora também
```

```python
def test_data_da_pagina_substitui_data_textual_da_serpapi(amb, monkeypatch):
    """A string '11 de ago. de 2025' do Google não pode bloquear o datePublished ISO da página."""
    # montar peça web com data_publicacao="11 de ago. de 2025" e CorpoLido(data_pub="2025-08-11T09:00:00-03:00");
    # após _ler: p["data_pub"] == "2025-08-11", p["data_pub_precisao"] == "dia",
    # p["data_pub_bruta"] == "2025-08-11T09:00:00-03:00"
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_aplicabilidade.py tests/test_pipeline.py -k "normalizar or data_da_pagina" -v`
  → Expected: FAIL (`ImportError: normalizar_data`).
- [ ] **Step 3: Implement** conforme as regras acima (sem dependência nova; meses PT-BR com e sem ponto).
- [ ] **Step 4: Run** `python3 -m pytest tests -q` → Expected: PASS.
- [ ] **Step 5: Medir cobertura (sem rede):** script descartável no scratchpad que reaplica `normalizar_data` aos
  `data_pub` de `runs/*/resultado.json` → Expected: parseáveis de ~20% para ≥ 60% (o resto é vazio de verdade,
  ~36%). Registrar o número na Task 8.
- [ ] **Step 6: Commit (só se pedido)** `feat: normaliza data de publicação (PT-BR, relativa, fuso) para o E4`

---

### Task 3: Data de referência determinística (relógio via `replay.*`) — esforço M

**Files:**
- Modify: `factcheck_mvp/replay.py` (nova `hoje`), `factcheck_mvp/aplicabilidade.py:48-71` (semântica de
  `referencia`), `factcheck_mvp/schemas.py:21-26` (`EntradaConsulta.data_referencia`),
  `factcheck_mvp/pipeline.py:516-517`, `eval/run.py:246`, `eval/README.md`
- Test: `tests/test_aplicabilidade.py`, teste de replay existente, `tests/test_pipeline.py`

**Interfaces:**
- `replay.hoje(contexto: str) -> str | None`: live → data atual UTC−3; record → idem e grava cassete
  `chave("GET", "relogio://hoje", contexto)` com `{"data": "YYYY-MM-DD"}` (reusa se já existir, como todo
  record); replay → lê; **miss → `None`** + `telemetria.fallback("relogio", "data de referência não gravada")`
  (não levanta `ReplayMiss`: o caso roda, só sem E4).
- `aplicabilidade.hoje_brt() -> str`; `dias_excedentes(..., referencia=None)` → **`None`** (referência
  desconhecida = não mede). `data_compativel(texto, data_pub, referencia=AGORA)` mantém o default "agora" por
  compatibilidade com o teste E1 existente (`AGORA` = sentinela que resolve para `hoje_brt()`).
- `EntradaConsulta.data_referencia: Optional[str] = None` (YYYY-MM-DD, validado).
- Pipeline: `ref = entrada.data_referencia or replay.hoje(contexto=texto_base)`; etapa `recebimento` detalha
  `marcador temporal: <marcador> (janela Nd); referência <data> (origem: entrada|relogio|ausente)`.
- `eval/run.py:246`: `EntradaConsulta(..., data_referencia=caso.get("data_referencia"))`.

**Política do eval (anti-autoengano):** padrão = relógio gravado no record (reproduz o que o sistema fez ao vivo
no dia do record). Campo opcional `data_referencia` no caso só quando o texto descreve um momento conhecido
(ex.: = `data_checagem` de um caso ClaimReview), com justificativa na `nota`, decidido **antes** de rodar e
registrado em `ITERACOES.md`; nunca mexer depois de ver o resultado. Alternativa não recomendada: usar
`data_checagem` automaticamente para todos os casos ClaimReview — muda o cenário medido (fato contemporâneo vs.
texto recirculando) sem decisão explícita.

Cassetes antigos (2.890, sem relógio): em replay o E4 fica desligado com `fallback onde=relogio` contado nas
métricas, até o próximo record da subamostra (Task 8).

- [ ] **Step 1: Write the failing tests**

```python
def test_dias_excedentes_sem_referencia_nao_mede():
    from factcheck_mvp import aplicabilidade
    assert aplicabilidade.dias_excedentes("recebeu alta hoje", "2020-01-01", None) is None

def test_relogio_replay_grava_e_reproduz(tmp_path, monkeypatch):
    from factcheck_mvp import replay
    monkeypatch.setenv("CASSETES_DIR", str(tmp_path))   # replay.dir_cassetes(), replay.py:126-128
    with replay.modo("record"):
        d1 = replay.hoje("Bolsonaro recebeu alta do hospital hoje")
    with replay.modo("replay"):
        assert replay.hoje("Bolsonaro recebeu alta do hospital hoje") == d1
        assert replay.hoje("texto nunca gravado") is None   # + fallback onde=relogio

def test_pipeline_usa_data_referencia_da_entrada(amb, monkeypatch):
    # espiona decisao.decidir; EntradaConsulta(..., data_referencia="2026-01-15")
    # → ev.data_referencia == "2026-01-15" (não a data de hoje)
```

- [ ] **Step 2: Run** `python3 -m pytest tests -k "referencia or relogio" -v` → Expected: FAIL.
- [ ] **Step 3: Implement** (testes em `tests/test_replay.py`; cassete do relógio no mesmo `dir_cassetes()`).
- [ ] **Step 4: Run** `python3 -m pytest tests -q` → Expected: PASS.
- [ ] **Step 5 (opcional, P):** `telegram_bot.py`: mensagem encaminhada → `data_referencia` = data do
  encaminhamento original (`forward_origin.date` em UTC−3); `api.py:121`/`telegram_bot.py:312` (entrada por
  link) → `data_referencia` = `data_pub` normalizada da página lida, ou E4 desligado se ela não tiver data (A5).
- [ ] **Step 6: Commit (só se pedido)** `feat: data de referência do E4 explícita e gravada em cassete`

---

### Task 4: Marcadores temporais — exclusões, extensões, janelas configuráveis, escopo por afirmação — esforço M

**Files:**
- Modify: `factcheck_mvp/aplicabilidade.py:16-45`, `factcheck_mvp/config.py`, `factcheck_mvp/decisao.py:325`
  (`AfirmacaoDecisao.janela` opcional), `factcheck_mvp/pipeline.py` (monta a janela por afirmação)
- Test: `tests/test_aplicabilidade.py`, `tests/test_decisao.py`

**Interfaces:**
- `janela_temporal(texto) -> int | None` (mesma assinatura) com:
  - **Exclusões** (não são marcador de momento): "hoje em dia", "até hoje", "até agora", "a partir de agora",
    "de agora em diante", "agora em <mês>" (tratado como mês: janela `E4_JANELA_MES`).
  - **Extensões:** "esta/nesta manhã|tarde|noite|madrugada" → hoje; "há pouco", "há instantes", "há poucas
    horas", "agora há pouco" → hoje; "anteontem" → `E4_JANELA_ANTEONTEM` (4); "neste/nesta
    domingo|segunda|…|sábado(-feira)", "neste fim de semana", "na última <dia>", "no último <dia>" → semana (8);
    "semana passada" → `E4_JANELA_SEMANA_PASSADA` (15); "neste mês", "este mês" → `E4_JANELA_MES` (32).
  - Janelas lidas de `config.E4_JANELA_*` (env), defaults iguais aos de hoje para os marcadores atuais.
- `janela_da_afirmacao(af_texto: str, texto_usuario: str, n_afirmacoes: int) -> int | None`: marcador na própria
  afirmação; senão, marcador do texto só se `n_afirmacoes == 1`; senão `None` (A3: "Fulano disse hoje que X
  aconteceu em 2019" com 2 afirmações não desconta X).
- `AfirmacaoDecisao.janela: Optional[int] = None`; `decidir` usa `af.janela` quando presente e cai para
  `janela_temporal(ev.texto_usuario)` só se o snapshot é antigo (compat).

- [ ] **Step 1: Write the failing tests** (frases fora do eval)

```python
@pytest.mark.parametrize("texto,janela", [
    ("Hoje em dia ninguém lê jornal", None),
    ("Até hoje a obra não terminou", None),
    ("A partir de agora o Pix será taxado", None),
    ("O ministro caiu esta manhã", 2),
    ("Há pouco o presidente renunciou", 2),
    ("Anteontem houve um terremoto em Natal", 4),
    ("Nesta segunda-feira o STF decidiu", 8),
    ("Neste domingo houve apagão em SP", 8),
    ("Na semana passada o dólar bateu R$ 7", 15),
    ("Bolsonaro recebeu alta do hospital hoje", 2),
])
def test_janela_marcadores(texto, janela):
    assert aplicabilidade.janela_temporal(texto) == janela

def test_janela_por_afirmacao_nao_vaza_para_outra_frase():
    t = "O deputado disse hoje que a ponte caiu em 2019. A obra custou 2 bilhões."
    assert aplicabilidade.janela_da_afirmacao("A obra custou 2 bilhões", t, 2) is None
    assert aplicabilidade.janela_da_afirmacao("Bolsonaro recebeu alta do hospital", "Bolsonaro recebeu alta do hospital hoje", 1) == 2
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_aplicabilidade.py -v` → Expected: FAIL nos novos.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python3 -m pytest tests -q` → PASS; conferir que os 3 casos do eval com marcador (A3)
  mudam só o esperado pelas regras gerais (`cr_el_nino_catastrofe_setembro` passa de janela 2 para mês) —
  registrar, não ajustar.
- [ ] **Step 5: Commit (só se pedido)** `feat: marcadores temporais do E4 com exclusões e escopo por afirmação`

### Task 4b (APROVADA pela usuária em 09/10; executar após medir a Task 4): datas explícitas — esforço M/G

"dia 8", "8 de outubro", "08/10" ancoram o **evento** numa data D (ano inferido = ocorrência mais recente ≤
referência). Excedente passa a ser `max(0, (D − folga) − data_pub)` (fonte bem anterior ao evento = outro
episódio); fonte posterior a D não é descontada. Exige generalizar `dias_excedentes` para receber um
`marco: (data_evento, janela)` em vez de só janela. Fora do E4 mínimo: risco de confundir "dia 8" (data do fato)
com data citada (prazo, aniversário). Se aprovada, mesmo formato TDD com frases fora do eval.

---

### Task 5: Gate do E1 alinhado (continua binário) — esforço P

**Files:** `factcheck_mvp/aplicabilidade.py:74-93` (`e_aplicavel(..., referencia, janela)`),
`factcheck_mvp/pipeline.py:552-562`; Test: `tests/test_aplicabilidade.py`, `tests/test_pipeline.py`.

**Decisão: manter binário (excedente == 0 ⇔ `r == 1`), não `r ≥ limiar`.** Motivo: o gate não pesa evidência,
decide **custo** — pular a web. Erro "aplicável demais" (checagem de outro episódio dispensa a busca) deixa a
decisão só com evidência descontada e sem nenhuma fonte atual: perda grande e silenciosa. Erro "inaplicável
demais" custa uma busca SerpAPI e nada mais. Com perdas tão assimétricas, o limiar ótimo fica no extremo
(qualquer excedente barra). Binário também desacopla o gate da curva que a usuária escolher na Task 0.

O que muda: o gate passa a usar **as mesmas entradas** da decisão — janela por afirmação (Task 4), data
normalizada (Task 2), referência resolvida (Task 3); hoje usa `afs[ai].texto` e "agora" (A3/A4).
Referência desconhecida → gate não barra por data (igual a hoje com data ausente).

- [ ] **Step 1: Failing test:** afirmação extraída **sem** "hoje" ("Bolsonaro recebeu alta do hospital"), texto do
  usuário com "hoje", checagem de 2025 → `e_aplicavel(...)[1] == "data incompatível"`; mesma checagem com
  `referencia=None` → não barra por data.
- [ ] **Step 2:** `python3 -m pytest tests/test_aplicabilidade.py tests/test_pipeline.py -q` → FAIL.
- [ ] **Step 3:** implementar; evento `fonte estagio=aplicabilidade` ganha `janela`, `excedente`, `referencia`.
- [ ] **Step 4:** PASS (inclui os 5 testes E1 antigos).
- [ ] **Step 5: Commit (só se pedido)** `fix: gate E1 usa a mesma janela, data e referência do E4`

---

### Task 6: Superfície ao usuário (bot, API, web) sem violar a neutralidade — esforço M

**Files:** `factcheck_mvp/decisao.py:154-192` (`justificativa`, `why_1linha`), `factcheck_mvp/schemas.py:44-60`
(`FonteEvidencia.data_pub_bruta`, `relevancia_temporal: Optional[float]`), `factcheck_mvp/pipeline.py:975-990`
(`_fontes`), `:1002-1006` (`_relatorio`: limitações + perguntas), `factcheck_mvp/agregador.py:33-37`
(`perguntas_guia(priorizar_data: bool = False)`), `factcheck_mvp/telegram_bot.py:226-236`,
`factcheck_mvp/api.py:153-176`; Test: `tests/test_decisao.py`, testes de bot/API existentes.

Onde e o quê:
- **`justificativa()`** (todas as superfícies): com `travas["data_incompativel"]`, frase
  `"N fonte(s) foram publicadas antes do período que o texto descreve (\"hoje\", \"ontem\"…); o peso delas foi
  reduzido porque podem tratar de outro episódio."` + se `nivel != nivel_sem_desconto`:
  `"Verifique se não é notícia antiga recirculando."`
- **`why_1linha()`** (1ª linha do bot): sufixo curto só quando o desconto **mudou** o nível.
- **Limitações** (bot mostra 3): `"Datas: N fonte(s) anteriores ao período do texto tiveram o peso reduzido."`
  Bits **não** vão para o bot (técnico demais); ficam no JSON da API (`decisao.descontos_temporais`, já
  serializado por `to_dict`) e no `<details>` "Como chegamos aqui" da web.
- **Perguntas-guia:** com desconto, a pergunta já existente "Compare a data do fato com a data da publicação…"
  (`agregador.py:34`) vai para o topo.
- **Por fonte** (bot e web): `📅 publicada em DD/MM/AAAA · anterior ao período do texto` quando descontada;
  data sempre a `data_pub_bruta` original ou a normalizada formatada.
- O aviso é sobre **datas**, nunca sobre veracidade: nada de "falso/verdadeiro"; selo continua só no formato
  atribuído (E5, fora daqui).

- [ ] **Step 1: Failing tests**

```python
def test_e4_aviso_chega_ao_usuario_e_e_neutro(monkeypatch):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    itens = [ItemEvidencia(url=f"https://{d}/a", cluster=d, classe="SUSTENTA", motor=JUIZ,
                           citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2021-07-18")
             for d in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    d = decidir(_ev_hoje(itens))
    texto = d.header() + " " + d.why_1linha() + " " + d.justificativa()
    assert "outro episódio" in d.justificativa() and "notícia antiga recirculando" in texto
    assert verificar_neutralidade(texto) == []
```
  + teste do `telegram_bot.formatar` com uma `FonteEvidencia(relevancia_temporal=0.05, data_pub="2021-07-18")`:
  linha "📅" presente, `verificar_neutralidade(saida) == []`, saída ≤ 3900 chars.
- [ ] **Step 2:** `python3 -m pytest tests -k "aviso or formatar or neutral" -q` → FAIL.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** `python3 -m pytest tests -q` → PASS (inclui a varredura de neutralidade existente).
- [ ] **Step 5: Commit (só se pedido)** `feat: aviso de data no bot, API e web (E4)`

---

### Task 7: Telemetria, `cli trace` e ferramentas de medição — esforço M

**Files:** `factcheck_mvp/pipeline.py:942-950` (`_emitir_decisao`), `factcheck_mvp/decisao.py:198-209`
(`resumo_trace`), `docs/TELEMETRIA.md` (tabela de eventos e de `fallback`), `eval/decisao.py:51-95` (CLI),
`eval/run.py:456-507` (`calcular_metricas`); Test: testes de telemetria/eval existentes.

- **Por fonte descontada:** `telemetria.evento("fonte", url, estagio="data", decisao="descontada",
  motivo=f"{dias} dias além da janela de {janela}", afirmacao, data_pub, data_pub_bruta, precisao, r, bits)` —
  emitido pelo pipeline a partir de `dec.descontos_temporais` (o `decidir` segue puro).
- **Decisão:** `resumo_trace()` ganha `"L_sem_desconto"`, `"nivel_sem_desconto"` e
  `"descontos": ["<url[:60]> +<dias>d r=<r> −<bits>b", …][:6]`; `telemetria.resumir_run` já imprime `travas`
  → `cli trace <id>` mostra o desconto sem mudança no CLI. `cli trace <id> --eventos fonte` lista os
  `estagio=data`.
- **Recebimento:** detalhe da etapa com marcador/janela/referência/origem (Task 3).
- **Fallbacks novos documentados:** `relogio` (referência não gravada), `data_pub` (formato não reconhecido).
- **Eval:** `metricas.e4 = {casos_com_marcador, casos_com_desconto, casos_nivel_mudou, bits_descartados_total,
  referencia_ausente}`; `python3 -m eval.decisao --snapshot X --sem-e4` (zera `texto_usuario`/`janela` antes do
  `decidir`, para A/B honesto sem flag no código de produto); `python3 -m eval.decisao --gerar-snapshot
  --resultado eval/resultados/<ts>` (lê `casos.jsonl` → `run_id` → evento `evidencias` via
  `decisao_gerar.evidencias_do_trace`).

- [ ] **Step 1: Failing tests:** `resumo_trace()` com desconto contém `"descontos"` e `"L_sem_desconto"`;
  `eval.decisao --sem-e4` num snapshot sintético de 1 caso com data antiga dá `L` maior em módulo que sem a flag;
  `--gerar-snapshot --resultado` num diretório de fixture com 1 trace produz 1 linha com `data_referencia`.
- [ ] **Step 2:** FAIL. **Step 3:** implementar + atualizar `docs/TELEMETRIA.md`/`eval/README.md`.
- [ ] **Step 4:** `python3 -m pytest tests -q` → PASS.
- [ ] **Step 5: Commit (só se pedido)** `feat: telemetria e medição do desconto temporal (E4)`

---

### Task 8: Medição + regressão Bolsonaro + registro — esforço M (+ nível 3 só com aprovação)

**Files:** `tests/test_pipeline.py` (regressão com a base real), `eval/ITERACOES.md`, `eval/snapshots/` (snapshot
novo), `docs/PROXIMOS_PASSOS.md` (E4 ✅, §5.2 resolvida pela decisão de 09/10).

- [ ] **Step 1: Regressão Bolsonaro nas duas direções (offline, sem rede):**
  teste com `Indice.de_checagens()` **real**, `FakeSerp([])`, juiz falso que devolve REFUTA com citação para as
  checagens Boatos.org (FALSO, 2026-01-14 e 2025-04-23, A8), `data_referencia="2026-10-09"`,
  entrada "Bolsonaro recebeu alta do hospital hoje" → `propensao != "alta"`,
  `decisao["travas"]["data_incompativel"] is True`, aviso no `justificativa`, web **não** pulada.
  Complementa `test_e4_fonte_antiga_que_confirma_nao_crava_baixa` (direção SUSTENTA). Stub de `r` = 0.05.
- [ ] **Step 2: Nível 0, guarda de regressão:** `python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl`
  → Expected: **idêntico** ao estado de partida (15,9% / 30,4% / grave 2 / indet 65,2%) — snapshot sem datas.
  Qualquer diferença = E4 vazando para casos sem marcador → investigar antes de seguir.
- [ ] **Step 3: Pedir aprovação** à usuária para o record da subamostra (nível 3, ~50 buscas SerpAPI; reusa
  cassetes existentes e grava o que falta, inclusive o relógio):
  `python3 -m eval.run --modo record --split dev --nome e4-sub20-record` filtrado pelos ids de
  `eval/subamostra20.txt`. **Não rodar sem o "sim".**
- [ ] **Step 4 (após o record):** replay duas vezes
  `python3 -m eval.run --modo replay --nome e4-sub20-replay` → Expected: as duas idênticas;
  `metricas.e4.referencia_ausente == 0`; `http_miss_total` igual ao do record; olhar `metricas.descoberta.pulada`
  antes de concluir qualquer coisa.
- [ ] **Step 5:** `python3 -m eval.decisao --gerar-snapshot --resultado eval/resultados/<ts>-e4-sub20-replay
  --saida eval/snapshots/e4-sub20.jsonl`; depois `--snapshot eval/snapshots/e4-sub20.jsonl` com e sem `--sem-e4`
  → tabela A/B por caso; `cli trace <run_id>` de cada caso cujo nível mudou.
- [ ] **Step 6: Critérios de aceitação** (todos):
  - `pytest` verde;
  - a3-dev idêntico (Step 2);
  - sub20: **nenhum erro grave novo** com E4 vs `--sem-e4`; todo caso que mudou de nível tem desconto
    explicado no trace (data da fonte, janela, r) e foi para `media`/`indeterminada` ou para a direção das
    fontes **dentro** da janela — nunca para um extremo sustentado só por fonte descontada;
  - `|L|` com E4 ≤ `|L_sem_desconto|` em todos os casos (simetria na prática);
  - replay determinístico (Step 4);
  - regressão Bolsonaro nas duas direções verde.
- [ ] **Step 7: Registrar em `eval/ITERACOES.md`:** "Iteração E3+E4" com hipótese (fonte de outro episódio
  não deve decidir fato apresentado como atual; desconto informacional não eleva propensão), mudanças
  (Tasks 0-7), métricas antes/depois (a3-dev; sub20 com/sem E4), cobertura de datas (Task 2 Step 5), `run_ids`
  olhados, e o que **não** foi validado: holdout (nível 4; só `cr_video_lula_ministros_stf_atual` tem
  marcador), amostra pequena (2 casos dev com marcador), curva `r` não calibrada.
- [ ] **Step 8: Commit (só se pedido)** `docs: mede E4 (relevância temporal) no nível 0 e na subamostra`

---

## Ordem e esforço

| # | Task | Esforço | Depende de |
|---|---|---|---|
| 0 | ✅ `relevancia_temporal` hiperbólica | P | — |
| 1 | Correções do desconto + contrafactual | M | 0 (testes usam stub; pode começar em paralelo) |
| 2 | Normalização de datas + prioridade | M | — |
| 3 | Referência determinística (relógio) | M | — |
| 5 | Gate E1 alinhado (binário) | P | 2, 3 (e 4 para a janela por afirmação) |
| 4 | Marcadores + escopo por afirmação | M | — |
| 4b | Datas explícitas (aprovada) | M/G | 4 (medida) |
| 6 | Superfície ao usuário | M | 1 |
| 7 | Telemetria e ferramentas de medição | M | 1, 3 |
| 8 | Medição, regressão, registro | M (+ nível 3 com aprovação) | todas |

Sequência sugerida: 1 → 2 → 3 → 4 → 4b → 5 → 6 → 7 → 8 (Task 0 já feita). Fechar cada task com `pytest` verde.

## Fora de escopo (explícito)

- Qualquer sinal de "notícia antiga fora de contexto" que **eleve** a propensão (decisão de 09/10: não).
- Trocar ou calibrar a curva `r` (hiperbólica escolhida; calibrar com pares rotulados fica para depois).
- Data **do fato dentro do corpo** da matéria (≠ data de publicação). (Task 4b foi aprovada e está no escopo.)
- Corrigir o fuso no ingestor/`checagens.jsonl` (normaliza-se na leitura; a base fica como está).
- Ampliar o dataset com casos de marcador temporal (decisão da usuária, §5.7) e rodar o holdout (nível 4).
- E2 (`onde_encontrado`), E5 (selo atribuído), Fases B/C/D.
- Mudar pesos (`W_POSTURA`, `W_VEREDITO`, τ) ou fatores de confiabilidade.

## Self-Review

1. **Cobertura do pedido:** qualidade de `data_pub` → A1/A2 + Task 2; marcadores → A3 + Task 4/4b; determinismo
   → A4 + Task 3; interação E1 → Task 5 (binário, justificado); superfície → Task 6; telemetria → Task 7;
   medição/aceitação/registro → A7/A8 + Task 8; ordem/esforço/fora de escopo → seções finais.
2. **Corpo de `relevancia_temporal`:** não escrito; só contrato e decisões da usuária (Task 0). Testes novos usam
   `monkeypatch`.
3. **Consistência de tipos:** `normalizar_data -> tuple[str, str] | None`; `replay.hoje -> str | None`;
   `dias_excedentes(referencia=None) -> None`; `Decisao.nivel_sem_desconto/log_odds_sem_desconto`;
   `AfirmacaoDecisao.janela: Optional[int]`; `EntradaConsulta.data_referencia: Optional[str]`.
4. **Anti-autoengano:** nenhum caso/rótulo/baseline editado; campo opcional de caso só com nota e antes de rodar;
   marcadores testados com frases fora do eval; aceitação inclui "nenhum grave novo" e A/B `--sem-e4`.
