# Fases E e B: checagem cruzada (sem E4)

## Goal
Fechar o fluxo de checagem cruzada definido pela equipe para depois da busca:
1. o avaliador LLM diz a relação de cada página com a afirmação e explica o raciocínio;
2. o BERTimbau dá fake/true com a probabilidade de cada página;
3. o juiz combina os dois;
4. as afirmações do mesmo texto são combinadas por conjunção.

O E4 tem plano próprio em `docs/superpowers/plans/2026-10-09-e4-relevancia-temporal.md`.

## Regras para quem executa (OpenCode)
- Leia `CLAUDE.md` e `docs/PROXIMOS_PASSOS.md` antes de começar.
- `python3 -m pytest tests -q` precisa estar verde antes e depois de cada task.
- Teste primeiro: escreva o teste que falha, depois implemente.
- Commits:
  - um commit por task;
  - **sem** a linha `Co-Authored-By` (preferência da usuária);
  - nunca commitar `factcheck_mvp/data/catalogo_propostas.json`.
- Proibido:
  - ler `bot.log` ou imprimir chaves do `.env`;
  - editar casos, rótulos ou baseline para o eval passar;
  - carregar outro modelo no Unsloth Studio.
- Todo fallback novo emite `telemetria.fallback(onde, motivo)`.
- Todo I/O novo passa por `replay.*`.
- Rodar eval `--modo record` ou `--split holdout` gasta SerpAPI. **Peça aprovação antes.**

## Decisões tomadas (09/10, pela usuária)
- **D1:** o BERTimbau mede a **credibilidade da página**, e a direção vem do avaliador.
  - Exemplo: uma checagem "É falso que X" é classificada como *true*, e o avaliador diz REFUTA. A afirmação provavelmente é falsa.
  - O modelo foi treinado com **texto curto**, então a página é dividida em blocos de `max_length` (192) tokens e a média dos blocos vira `prob_fake`.
- **D2:** um texto com parte claramente falsa e parte verdadeira dá **alta**. A justificativa cita as duas partes, a falsa e a verdadeira.
- **Record:** gravar a subamostra **só depois da T4**, porque o prompt novo do avaliador invalida o cassete. Um record só.

## Tasks
- [x] **T0. Commitar o E3 e o E4 já feitos.** ✅ FEITO (09/10, commit `f7841e3`). A `relevancia_temporal` está pronta (hiperbólica) e o pytest dá 387 passed.
  - Commitar o E3 que já está no working tree (`decisao.py`, `aplicabilidade.py`, `pipeline.py`, `eval/decisao.py`, testes) e `eval/subamostra20.txt`.
  - → Verify: pytest verde; `git status` limpo.

- [x] **T1. E3 visível para o usuário.** ✅ FEITO (commit `a4a1fda`; review spec ✅). Decisão registrada: frase literal só no HTML/bot; JSON expõe `corpo_lido` + `decisao.nao_analisadas` (follow-up se quiser literal no JSON). A fonte com `corpo_lido=False` aparece como "não analisada integralmente" no bot (`telegram_bot.py`), na API (`api.py`) e na web.
  - Use `Decisao.nao_analisadas`.
  - → Verify: novo teste em `tests/test_comunicacao.py` com 1 fonte lida e 1 só título; o texto contém "não analisada integralmente" só para a segunda.

- [x] **T2. E2 "onde foi encontrado".** ✅ FEITO (commit `b8f2dce`; review spec ✅).
  - Adicionar `onde_encontrado: Literal["base", "web"]` a `RelatorioChecagem` (`schemas.py:100`).
  - Preencher em `pipeline.py` a partir de `pulou_web` / `houve_aplicavel`.
  - Na web, mostrar o aviso "Não encontrado na base de checagens; resultado de outras fontes".
  - → Verify: testes `test_base_aplicavel_pula_web` e `test_base_inaplicavel_chama_web` checam `rel.onde_encontrado`.

- [x] **T3. E5 selo atribuído.** ✅ FEITO (commit `3697edb` + fix `bd2daa3` sem cache + 4º teste; review + re-review clean).
  - O selo sempre aparece como "Selo d{o,a} <agência>: <SELO>" (`agregador.py`).
  - `verificar_neutralidade` (`agregador.py:40`) ignora **só** esse formato, e só com agência cadastrada no catálogo.
  - → Verify:
    - o teste aceita "Selo da Lupa: FALSO";
    - o teste reprova "é falso" na nossa conclusão;
    - o teste reprova "Selo do Blog X: FALSO" (agência não cadastrada).

- [x] **T4. B1a: saída do avaliador.** ✅ FEITO (commit `95e3a49` + fix `cf7e728` cobrindo `_normalizar`; review + re-review clean). Cassete novo fica para a T9.
  - O prompt de `avaliador.py` / `juiz_llm.py` passa a pedir:
    - a categoria de relação (SUSTENTA / REFUTA / RELATA_SEM_ENDOSSO / NAO_TRATA);
    - `raciocinio` de até ~240 caracteres. O limite vai **só no prompt**, sem truncar no código.
  - O retorno de `avaliar()` ganha `raciocinio`, propagado até `Fonte` em `schemas.py`.
  - Com o prompt novo, o cassete também muda (veja a T8).
  - → Verify: teste com LLM fake verifica que `raciocinio` chega em `rel.fontes[i]` sem corte.

- [x] **T5. B1b: BERTimbau por página** (D1 decidida: credibilidade, texto curto, blocos). ✅ FEITO (commit `b7c47bb`; review spec ✅ com 1 desvio aceito: `ModeloTreinadoDetector` faz chamada única — chunking 192/4 é específico do BERT).
  - Criar `DetectorFake.analisar_pagina(titulo, corpo)` em `modelo_fake.py`:
    - dividir em blocos de `max_length` tokens (até 4 blocos);
    - `prob_fake` = média dos blocos.
  - Chamar em `pipeline._ler` só para páginas com `corpo_lido`, guardando `p["bert"] = {prob_fake, modelo}`.
  - Emitir `telemetria.evento("bert_pagina", url, prob_fake)`.
  - O mock continua para os testes.
  - → Verify:
    - um teste com o detector mock confirma que só as páginas lidas recebem `bert`;
    - `cli trace last --eventos bert_pagina` mostra os valores.

- [ ] **T6. B1c: o juiz combina avaliador e BERTimbau** (`decisao.py`). ⏳ IMPLEMENTADO MAS NÃO COMMITADO (09/10): hunks em `decisao.py`/`pipeline.py`/`eval/decisao.py`/testes sobrevivem no working tree, mas o arquivo está com syntax break do WIP concorrente do E4 e sem review — vai em follow-up, não neste PR. Medição offline já feita: `cr_jn_soltura_vorcaro` baixa-GRAVE→media; grave restante é de postura, fora do escopo.
  - Adicionar `ItemEvidencia.prob_fake_pagina`.
  - Peso da postura = `W_POSTURA × f_fonte × (1 − prob_fake_pagina)`. Página que o modelo acha fake quase não vota.
  - Selo ClaimReview **de página** deixa de votar. Isso corrige `cr_jn_soltura_vorcaro`, cujo selo vinha do template do SBT.
  - O selo do **índice** (via E1/aplicabilidade) continua votando.
  - Atualizar a docstring da fórmula.
  - Reescrever `test_selo_verdadeiro_do_fato_ou_fake_baixa`.
  - → Verify:
    - novo teste: página com prob_fake 0,9 e REFUTA pesa ≤ 0,1 de uma com prob_fake 0;
    - teste: selo de página sem índice não aparece em `vereditos_aplicados`.

- [ ] **T7. B2: combinação por conjunção** (D2 decidida: alta; `decisao.decidir`, bloco "combinação entre afirmações"). ⏳ NÃO INICIADO → follow-up.
  - Trocar a regra de máximo por `p_texto = 1 − Π(1 − σ(L_a))` e `L = logit(p_texto)`.
  - Afirmação sem voto entra com p = 0,5? **Não.** Ela fica fora do produto: ausência de evidência não é evidência.
  - Escrever os 2 testes antes de implementar:
    - (a) A: L=+2, B: L=−5 → **alta**, com a justificativa citando a parte falsa e a parte verdadeira;
    - (b) parte falsa abaixo de τ (p≈0,70) com parte verdadeira (p≈0,10) → **não** baixa (caso `cr_lula_acabar_bets_que_criou`).
  - → Verify: os dois testes passam, e `test_mutacao_*` continua passando.

- [ ] **T8. B5 e B6 (decisão e independência).** ✅ FEITO (commit `6453b3f` + fix `54c6d67` plural `imagens`; review + re-review clean; delta `eval.decisao` zero por construção).
  - B5: a trava VAGO (`pipeline.py:54`, `VAGO_RE`) só dispara quando o sujeito é genérico (economia, país, governo).
  - B6: em `corroboracao.py` / `catalogo.py`:
    - a assinatura de agência não casa com crédito de foto ("Folhapress");
    - "curada" exige host exato ou prefixo declarado.
  - → Verify:
    - "Ibuprofeno piora o quadro de dengue" não é vago;
    - `comentarios1.folha.uol.com.br` não é curada.

- [ ] **T9. Medição e registro.** ⏳ PARCIAL: `eval.decisao` antes/depois feito offline para T6 e T8 (deltas nos reports); `eval/ITERACOES.md` ainda sem a entrada desta rodada; **record (~60 buscas) e holdout (~60) PENDENTES de aprovação** (custo SerpAPI).
  - `python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl`: antes e depois de cada task de decisão (T6, T7, T8).
  - **Depois da T4 e com aprovação** (cerca de 60 buscas): `python3 -m eval.run --modo record --ids "$(cat eval/subamostra20.txt)" --max-buscas-serpapi 60 --nome fase-eb`, seguido de replay (`llm_miss=0`).
  - Registrar em `eval/ITERACOES.md`: hipótese, mudança, métricas antes/depois e run_ids.
  - **Holdout** uma única vez, no fim e com aprovação: `--split holdout` (~60 buscas). Reportar dev e holdout lado a lado.
  - → Verify: a entrada no ITERACOES tem as duas tabelas.

## Ordem
- T0 vem primeiro.
- T1, T2 e T3 são independentes e podem ir em qualquer ordem.
- Depois, a sequência T4 → T5 → T6 → T7, que mexem em decisão; T8 pode ir em paralelo.
- T9 por último.

**Caminho crítico:** T4 → T5 → T6 → T9 (record).

## Status 09/10 (PR parcial)
- ✅ Commitados e revisados (spec ✅): T0, T1, T2, T3(+fix), T4(+fix), T5, T8(+fix).
- ➕ Extra emergencial commitado (`d06ebce`): `replay.hoje()` (relógio via cassete, spec E4-Task-3 verbatim) — sem ele o stack T0–T5 não era autocontido (32 falhas `AttributeError`); E4-Task-3 deve rebasar/estender a partir daqui.
- ⏳ Follow-up (fora deste PR): T6 (implementado, sem commit/review), T7 (não iniciado), T9-record/holdout (aguardando aprovação).

## Done When
- [x] pytest verde (421 passed no worktree limpo do PR), e nenhum erro grave novo no eval de decisão (T8 delta zero; T6 −1 grave; T4/T5 sem mudança de nível).
- [ ] `cr_jn_soltura_vorcaro` e `cr_lula_acabar_bets_que_criou` deixam de ser erro grave. ⏳ PARCIAL: vorcaro corrigido pelo T6 (fora deste PR); bets continua grave por posturas (caso T7).
- [x] Bot e web mostram: onde foi encontrado (T2); selo atribuído (T3); fontes não analisadas integralmente (T1). Raciocínio do avaliador no bot/web: ⏳ pendente (T4 fia até `Fonte`; render é follow-up).
- [ ] `eval/ITERACOES.md` tem números de dev e de holdout. ⏳ pendente (T9).

## Notes
- **B4** ("agência sozinha nunca chega a alta") foi absorvido pela T6. Reavalie depois de medir.
- O BERTimbau precisa de `torch` e `transformers`. Ele carrega no boot (`carregar_detector`) e já hoje falha com uma mensagem clara.
- Custo previsto: T9 record ~60 buscas mais holdout ~60. O cap diário é 100, então faça em dias diferentes.
