---
description: Roda n rodadas do loop eval → trace → hipótese → mudança → eval e registra em eval/ITERACOES.md
argument-hint: "[n=1]"
---

Execute **$ARGUMENTS** rodada(s) (padrão 1) do loop de melhoria descrito no `CLAUDE.md`. Uma rodada:

1. **Estado atual.** `python3 -m pytest tests -q` (tem que passar) e
   `python3 -m eval.run --nome iter<N>-antes` (modo replay, split dev). Anote acerto, erro_grave,
   taxa_indeterminada, `descoberta` (rodou/pulada) e `http_miss_total`. Se `http_miss_total > 0`, os
   casos com miss não estão medindo o sistema: grave com `--modo record` antes de concluir.
2. **Diagnóstico.** Leia `eval/resultados/<último>/relatorio.md`. Para 2–4 casos errados (erros graves
   primeiro), rode `python3 -m factcheck_mvp.cli trace <run_id>` e, se preciso,
   `--eventos llm,fallback,sinal,decisao`. Identifique a **etapa** culpada e o mecanismo
   (ex.: "sinal X com direção errada", "fallback Y marca relevante sem ler", "trava Z sobrescreve").
3. **Hipótese** em uma frase, testável, com previsão de quais casos mudam e em que direção.
4. **Mudança pequena** e geral (não específica de um caso). Se tocar prompt/query/página lida, o próximo
   eval precisa de `--modo record`; senão, replay. Adicione/ajuste teste unitário se a mudança for de lógica.
5. **Verificação.** `python3 -m pytest tests -q` e `python3 -m eval.run --nome iter<N> [--modo record] --gate`.
6. **Decisão.** Melhorou sem aumentar erro grave → mantenha e, se for o novo patamar, `--salvar-baseline`.
   Piorou ou não mudou o que previu → reverta a mudança e registre o aprendizado.
7. **Registro** em `eval/ITERACOES.md` (append), no formato:

   ```
   ## Iteração N — <título curto> (<data>)
   - Hipótese: …
   - Evidência (run_ids / eventos): …
   - Mudança: arquivos e resumo
   - Métricas dev (antes → depois): acerto a→b, erro_grave a→b, indeterminada a→b, fallbacks …
   - Modo: replay|record · buscas SerpAPI live: n
   - Decisão: mantida|revertida · baseline atualizado? sim|não
   ```

Ao terminar todas as rodadas: rode `python3 -m eval.run --split holdout --nome holdout-<data>` e reporte
dev × holdout. Regras: não editar casos/baseline para passar; não ajustar pesos para um caso; todo
fallback novo emite `telemetria.fallback`; não carregar outro modelo no Unsloth Studio; não commitar sem
pedido; se aparecer fallback de cota da SerpAPI, pare e peça nova chave ao usuário.
