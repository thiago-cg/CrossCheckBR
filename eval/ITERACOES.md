# Log de iterações

Formato de cada entrada: ver `.claude/commands/iterar.md`. Métricas do split `dev`, perfil busca aberta
(`INDICE_CHECAGENS=0`) salvo indicação.

## Iteração 0 — sistema original (2026-09-28)

Medição "antes" das correções das fases 2–4. Código: `main@ed1d168` + diff local do dono
(`modelo_fake.py`, `catalogo.json`) + só a instrumentação da fase 1 (sem mudança de lógica).

- Comando: `python3 -m eval.run --modo record --split dev --nome antes --salvar-baseline --paralelo 3 --timeout-caso 400`
- Resultado: `eval/resultados/20260928-170220-antes/` · baseline: `eval/baseline.json`
- Config: padrão (`SERP_ESTRATEGIA=agente`, `AGENTE_MAX_BUSCAS=8`), `INDICE_CHECAGENS=0` passado ao eval,
  mas **o índice ainda não tem esse gate** (a flag não teve efeito: o índice local rodou, ex. "5 checagens"
  em urnas). LLM local `unsloth/LFM2.5-8B-A1B-GGUF`; **sem `OPENROUTER_API_KEY`** → juiz sempre em
  fallback léxico; detector = mock (`FAKE_MODEL_PATH` vazio).
- Descoberta web: **rodou em 12/12** (a `SERPAPI_KEY` entrou no `.env` antes da medição). A limitação
  prevista "sem chave, descoberta pulada" não se aplicou; a limitação real desta medição é o **juiz ausente**.
- SerpAPI: 33 buscas live neste eval (+3 no smoke test da CLI antes do contador) = 36 em `eval/serpapi_uso.json`.
- Replay conferido: `python3 -m eval.run --modo replay` reproduz os 12 níveis em 0,5 s, sem rede
  (`http_miss=5` = requisições que falharam por rede/SSL no record e por isso não têm cassete; o
  resultado é o mesmo).

| métrica | valor |
|---|---|
| n | 12 |
| acerto | 0,333 (4/12) |
| acerto + parcial | 0,333 |
| **erro grave** | **0,333 (4/12)**: cafe_cura_cancer, vacina_altera_dna, urnas_fraudadas_2022, satira_feriado (falso→baixa) |
| indeterminada | 0,417 |
| níveis | baixa 7, indeterminada 5, média 0, alta 0 |
| por rótulo | falso 0/7 (4 graves, 3 indeterminada) · verdadeiro 3/4 · sem_evidencia 1/1 |
| LLM por caso | 2,0 (afirmações + padrões, locais); p50 39,9 s, p95 56,3 s por caso |
| fallback (fração de casos) | juiz 100% (10 itens/caso), openrouter.chat 100%, descoberta-site.jev 92%, padroes 58%, afirmacoes 8%, juiz.lexico-top3 8% |
| descartes | corpo curto/JS/paywall 13, fora do catálogo 9, HTTP 403 7, SSL 2 |

O que os traces mostram:

1. **Nenhum caso chega a "alta"**: todo `baixa` vem de um único sinal, "cobertura ampla" (−0,5), montado
   sobre fontes marcadas relevantes pelo fallback léxico (sem juiz). É o C1+C3 do review ao vivo: falar
   do tema reduz a propensão. Os 3 acertos em `verdadeiro` saem pelo mesmo mecanismo (acerto por acaso).
2. **A extração de afirmações corrompe a entrada**: o LLM local traduziu 2/12 para inglês ("Electronic
   voting machines were frauded…", "Government decrees…") e a busca foi feita em inglês; em
   `falso_que_vacina_dna` removeu o "É falso que" e passou a checar o próprio boato; em `CAFÉ CURA
   CÂNCER!!!` devolveu `VAZIO` (regex assumiu).
3. **Bug C5 confirmado ao vivo**: `padroes` cai no regex em 7/12 casos com
   `ValueError: not enough values to unpack` (o `return []` no ramo local quando o LLM responde NENHUM).
