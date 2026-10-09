# E1 Base Primeiro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pipeline consulta a base de checagens antes da web e pula a web quando há checagem aplicável.

**Architecture:** Novo módulo puro `factcheck_mvp/aplicabilidade.py` (`e_aplicavel` + `data_compativel`) consumido por `Pipeline._executar`: fase base (índice → `_ler` → `_julgar` só-base) → gate → fase web só se nenhuma aplicável; recibo `web pulada: checagem aplicável na base`.

**Tech Stack:** Python 3 (`python3`, não `python`), pytest, Pydantic v2, BM25 (`rank_bm25`), telemetria `runs/<id>/trace.jsonl`.

**Spec:** `docs/PROXIMOS_PASSOS.md` §4 Fase E / E1 (com E4-parcial só-gate + regressão Bolsonaro), mais contexto levantado em 07/10 (pipeline 298-335, índice `buscar_checagens`, juiz 4 classes, decisão `W_VEREDITO=1.5`).

## Global Constraints

- `python3 -m pytest tests -q` sempre verde antes e depois.
- Nunca editar casos, rótulos, `esperado` ou `eval/baseline.json` para passar; mudança deve ter razão geral, não acertar um caso.
- Sem `Co-Authored-By: Claude` nos commits; não commitar sem pedido (plano termina com commits locais por tema + PR só se pedido).
- Todo fallback novo emite `telemetria.fallback(onde, motivo)`; I/O novo usa `replay.*`.
- `INDICE_CHECAGENS` default `"1"` (`indice.habilitado()`); eval principal usa `0` — E1 deve funcionar com flag ligada e manter comportamento com flag desligada.
- Níveis de custo: 0 decisão offline (zero); subir para record/live só com aprovação e custo avisado; sem cassetes nesta máquina (`eval.run --modo replay` devolve não-reproduzidos).
- Base = Lupa, Boatos.org, Fato ou Fake, E-Farsas, Comprova, Aos Fatos, Estadão Verifica (via `data/checagens.jsonl`, 21.579 linhas).
- Não ler `bot.log`; nunca imprimir chaves do `.env`.

## Review Focus

- Checagem antiga devolvida para fato "de hoje" ("Bolsonaro recebeu alta hoje" vs Boatos.org 3 anos atrás) deve barrar aplicabilidade, não votar.
- Hit da base sem `veredito` (53% Lupa `rss-titulo`, `veredito=null`) nunca é aplicável mesmo com juiz SUSTENTA.
- Página da base julgada só pelo título (`corpo_lido=False`) nunca é aplicável.
- Juiz `RELATA_SEM_ENDOSSO` com selo FALSO não é aplicável se a página só relata sem endossar (regra 4 do juiz).
- Web deve ser pulada de verdade: nenhuma chamada SerpAPI, nenhum custo, etapa `pulada` auditável quando há aplicável.

---

## File Structure

- **Create `factcheck_mvp/aplicabilidade.py`** — única responsabilidade: decidir se uma peça da base é aplicável. Puro (sem I/O, sem LLM). Funções: `data_compativel(texto_usuario, data_pub) -> bool`, `e_aplicavel(...) -> tuple[bool, str]`.
- **Modify `factcheck_mvp/pipeline.py`** — única responsabilidade: reordenar `_executar` em fase-base → gate → fase-web-condicional. Extrai `_fase_base()` helper; resto (`_selecionar`, `_ler`, `_julgar`, `_ondas_extras`, `decidir`) inalterado.
- **Test `tests/test_aplicabilidade.py`** (novo) — unidade pura do gate.
- **Test `tests/test_pipeline.py`** (existente, adicionar 3 testes) — integração base-primeiro com juiz falso, sem rede.
- **Medir** com `python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl` + `python3 scripts/bench_indice_checagens.py` (sem `eval.run`; sem cassetes aqui).

---

### Task 1: Gate puro `aplicabilidade.py`

**Files:**
- Create: `factcheck_mvp/aplicabilidade.py`
- Test: `tests/test_aplicabilidade.py`

**Interfaces:**
- Consumes: `factcheck_mvp.selos.direcao(veredito: str | None) -> float`; `juiz_llm.TRATA = ("SUSTENTA","REFUTA","RELATA_SEM_ENDOSSO")`.
- Produces:
  - `data_compativel(texto_usuario: str, data_pub: str | None) -> bool`
  - `e_aplicavel(classe: str | None, citacao_verificada: bool | None, corpo_lido: bool, veredito: str | None, texto_usuario: str, data_pub: str | None) -> tuple[bool, str]`

- [ ] **Step 1: Write the failing test**

```python
def test_aplicavel_exige_corpo_citacao_selo_data():
    from factcheck_mvp import aplicabilidade
    ok, motivo = aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", "Café cura câncer", "2026-09-22")
    assert ok is True
    assert aplicabilidade.e_aplicavel("REFUTA", True, False, "FALSO", "Café cura câncer", "2026-09-22")[0] is False
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, None, "Café cura câncer", "2026-09-22")[0] is False
    assert aplicabilidade.e_aplicavel("NAO_TRATA", True, True, "FALSO", "Café cura câncer", "2026-09-22")[0] is False
    assert aplicabilidade.e_aplicavel("REFUTA", False, True, "FALSO", "Café cura câncer", "2026-09-22")[0] is False

def test_data_incompativel_barra_bolsonaro():
    from factcheck_mvp import aplicabilidade
    assert aplicabilidade.data_compativel("Bolsonaro recebeu alta do hospital hoje", "2020-01-01") is False
    assert aplicabilidade.data_compativel("Bolsonaro recebeu alta do hospital hoje", None) is True
    assert aplicabilidade.data_compativel("Café cura câncer", "2020-01-01") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_aplicabilidade.py -v`
Expected: FAIL with "No module named 'factcheck_mvp.aplicabilidade'" (ou collection error: file not found).

- [ ] **Step 3: Implement `factcheck_mvp/aplicabilidade.py`**

Regras (nesta ordem, primeiro `False` vence; motivo é string curta):
1. `corpo_lido is False` → `(False, "corpo não lido")`.
2. `classe not in ("SUSTENTA","REFUTA","RELATA_SEM_ENDOSSO")` → `(False, "juiz não trata do fato")`.
3. `citacao_verificada is not True` → `(False, "sem citação verificada")`.
4. `selos.direcao(veredito) == 0.0` (None, SATIRA, desconhecido) → `(False, "sem selo com direção")`.
5. `data_compativel(texto_usuario, data_pub) is False` → `(False, "data incompatível")`, senão `(True, "aplicável")`.

`data_compativel(texto_usuario, data_pub)`:
- Sem marcador temporal em `texto_usuario` → `True`. Marcadores (case-insensitive, sem acento): `hoje`, `agora`, `ontem`, `nesta semana`, `neste semana`, `acaba de`, `acabou de`.
- `data_pub` None/vazia/inparseável → `True` (não barra por falta de data).
- Parse: aceita `YYYY-MM-DD` e prefixo de ISO (`data_pub[:10]`); resto → `True`.
- Janelas (dias, da data atual UTC): `hoje|agora|acaba de|acabou de` → 2; `ontem` → 3; `nesta semana|neste semana` → 8. Incompatível se `(hoje_utc - data) > janela`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_aplicabilidade.py -v`
Expected: PASS (todos).

- [ ] **Step 5: Commit**

```bash
git add factcheck_mvp/aplicabilidade.py tests/test_aplicabilidade.py
git commit -m "feat: gate de aplicabilidade da base (E1)"
```

---

### Task 2: Pipeline base-primeiro com web condicional

**Files:**
- Modify: `factcheck_mvp/pipeline.py:298-335` (`_executar` §3-§4) + novo método `_fase_base`
- Test: `tests/test_pipeline.py` (adicionar)

**Interfaces:**
- Consumes: `Indice.buscar_checagens(afirmacao: str, k: int = 5) -> list[dict]`; `Pipeline._selecionar/_ler/_julgar` existentes (assinaturas inalteradas); `aplicabilidade.e_aplicavel(...)` da Task 1.
- Produces: `Pipeline._fase_base(afs, pecas_base) -> tuple[dict, bool]` (retorna `(julg_base, houve_aplicavel)`); etapa recibo `base-checagem` existente + nova etapa `descoberta` com `status="pulada"` e detalhe `"web pulada: checagem aplicável na base"`.

- [ ] **Step 1: Write the failing tests** (acrescentar em `tests/test_pipeline.py`, reusar `amb`, `_rodar`, `_pag`, `FakeSerp`)

```python
def test_base_aplicavel_pula_web(amb, monkeypatch):
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    url = "https://www.aosfatos.org/noticias/cafe-cura-cancer-falso/"
    idx = Indice.de_checagens([{"url": url, "titulo": "É falso que café cura câncer",
        "afirmacao_checada": "Café cura câncer", "selo_original": "Falso",
        "veredito": "FALSO", "agencia": "aos-fatos",
        "trecho": "É falso que café cura câncer, segundo especialistas."}])
    amb[url] = "É falso que café cura câncer, segundo especialistas. " * 50
    chamadas = {"n": 0}
    class Contadora(FakeSerp):
        def buscar(self, q):
            chamadas["n"] += 1
            return super().buscar(q)
    pipe = Pipeline(Catalogo.carregar(), idx, Indice(), serpapi=Contadora([]), detector=MockDetector())
    import asyncio
    rel = asyncio.run(pipe.executar(EntradaConsulta(tipo="titulo", conteudo="Café cura câncer")))
    assert chamadas["n"] == 0
    assert any(e.nome == "descoberta" and e.status == "pulada" and "checagem aplicável" in e.detalhe for e in rel.etapas)
    assert rel.propensao == "alta"

def test_base_inaplicavel_chama_web(amb, monkeypatch):
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    idx = Indice.de_checagens([{"url": "https://lupa.uol.com.br/x", "titulo": "Reportagem sobre café",
        "afirmacao_checada": "Café cura câncer", "selo_original": None, "veredito": None,
        "agencia": "lupa", "trecho": "Reportagem sobre café."}])
    r, corpo = _pag("https://g1.globo.com/x", "Café cura câncer? Checagem", "É falso que café cura câncer, segundo o INCA.")
    amb["https://g1.globo.com/x"] = corpo
    import asyncio
    rel = asyncio.run(Pipeline(Catalogo.carregar(), idx, Indice(), serpapi=FakeSerp([r]), detector=MockDetector()).executar(EntradaConsulta(tipo="titulo", conteudo="Café cura câncer")))
    assert not any(e.nome == "descoberta" and e.status == "pulada" and "checagem aplicável" in e.detalhe for e in rel.etapas)

def test_checagem_antiga_para_fato_de_hoje_nao_pula_web(amb, monkeypatch):
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    url = "https://boatos.org/x-antiga"
    idx = Indice.de_checagens([{"url": url, "titulo": "Bolsonaro alta hospital",
        "afirmacao_checada": "Bolsonaro recebeu alta do hospital", "selo_original": "Falso",
        "veredito": "FALSO", "agencia": "boatos-org", "data_pub": "2020-01-01",
        "trecho": "É falso que Bolsonaro recebeu alta do hospital, segundo apuração."}])
    amb[url] = "É falso que Bolsonaro recebeu alta do hospital, segundo apuração. " * 50
    import asyncio
    rel = asyncio.run(Pipeline(Catalogo.carregar(), idx, Indice(), serpapi=FakeSerp([]), detector=MockDetector()).executar(EntradaConsulta(tipo="titulo", conteudo="Bolsonaro recebeu alta do hospital hoje")))
    assert not any(e.nome == "descoberta" and e.status == "pulada" and "checagem aplicável" in e.detalhe for e in rel.etapas)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_pipeline.py -k "pula_web or inaplicavel or antiga_para_fato" -v`
Expected: FAIL (web ainda chamada no 1º; `CollectionError`/`AssertionError` nos novos testes).

- [ ] **Step 3: Implement reordenação em `factcheck_mvp/pipeline.py`**

Comportamento novo em `_executar` (manter nomes de etapa `base-checagem`/`descoberta`/`deep-crawl`/`juiz`):
1. Após afirmações, monta `pecas_base` só do índice (código atual §3, inalterado; `INDICE_CHECAGENS=0` → lista vazia + etapa `pulada`).
2. Novo `_fase_base(afs, pecas_base)`: `selecionar` restrito às peças base → `_ler` → `_julgar` → para cada `(pi,ai)` chama `e_aplicavel(classe, citacao_verificada, corpo_lido, veredito, afs[ai].texto, data_pub)`; emite `telemetria.evento("fonte", estagio="aplicabilidade", decisao=...)`; retorna julgamentos e `houve_aplicavel: bool`.
3. Gate: se `houve_aplicavel` → pula `_buscar_web` e `_ondas_extras`; `etapa("descoberta", "pulada", "web pulada: checagem aplicável na base", fontes_base[:5])`; `limitacoes.append("Web pulada: checagem aplicável na base.")`; segue para clusters/decisão só com peças base (+ `trecho` do índice quando sem corpo? não — aplicável exige corpo, então sem corpo não pula).
4. Senão → fluxo atual inalterado (web + dedupe + resto). `serpapi.ativo=False` mantém `descoberta pulada (sem SERPAPI_KEY)`.
5. Telemetria: `telemetria.fallback` só em erro real; gate normal é `evento("etapa", nome="descoberta", status="pulada", ...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_pipeline.py tests/test_aplicabilidade.py -q`
Expected: PASS (inclui os 13 antigos de `test_pipeline.py`).

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest tests -q`
Expected: PASS (363 + novos; zero falhas).

- [ ] **Step 6: Commit**

```bash
git add factcheck_mvp/pipeline.py tests/test_pipeline.py
git commit -m "feat: base primeiro, web só sem checagem aplicável (E1)"
```

---

### Task 3: Medição nível 0 + bench + registro (sem rede)

**Files:**
- Modify: `eval/ITERACOES.md` (adicionar entrada)
- Test: nenhum código novo (comandos de medida)

**Interfaces:**
- Consumes: `python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl`; `python3 scripts/bench_indice_checagens.py`.

- [ ] **Step 1: Rodar eval de decisão antes/depois**

Run: `python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl`
Expected: saída com `acerto`, `acerto+parcial`, `erro_grave`, `indeterminada`; comparar com referência `24,6% / 56,5% / 1 / 39%`. E1 sozinho pode não mover nível 0 (decisão pura não muda; o ganho é custo/web pulada) — registrar delta mesmo se zero.

- [ ] **Step 2: Rodar bench do índice**

Run: `python3 scripts/bench_indice_checagens.py`
Expected: `recall@3 ~0,86`, `FP ~7,9%`; sem regressão.

- [ ] **Step 3: Registrar em `eval/ITERACOES.md`**

Escrever entrada: hipótese (web pulada com aplicável economiza buscas sem perder acerto), mudança (Tasks 1-2), métricas antes/depois, run_ids dos 3 testes, nota explícita "eval.run completo não roda aqui (sem cassetes)".

- [ ] **Step 4: Commit**

```bash
git add eval/ITERACOES.md
git commit -m "docs: mede E1 no eval de decisão (nível 0)"
```

## Self-Review

1. **Spec coverage:** E1 (base→leitura→juiz→aplicabilidade, web condicional, recibo web pulada) → Tasks 1-2. Data compatível (E4-parcial, só barra) → Task 1 `data_compativel` + teste Bolsonaro. Selo com direção → `direcao != 0`. Citação verificada + corpo lido + mesmo fato → `e_aplicavel`. Fora de escopo de propósito: E2 (`onde_encontrado`), E3 (título não vota), E5 (selo atribuído), E4-elevação de propensão — não implementados aqui.
2. **Step scan:** cada step tem um artefato único (teste nomeado / comando + saída esperada / assinatura exata / commit). Sem "TBD".
3. **Type consistency:** `e_aplicavel(...) -> tuple[bool, str]` usado igual nas Tasks 1-2; `_fase_base(afs, pecas_base) -> tuple[dict, bool]`; etapa `descoberta/pulada` com detalhe fixo `"web pulada: checagem aplicável na base"`; `data_compativel(str, str|None) -> bool`.
4. **Review Focus:** as 5 linhas têm teste dono (Task 1: sem selo/corpo/citação/data; Task 2: web pulada de verdade + regressão Bolsonaro).
5. **Proportion:** plano menor que o código que Orquestra; corpos só onde assinatura+teste não determinam (regra 1-5 e janelas 2/3/8 dias).
