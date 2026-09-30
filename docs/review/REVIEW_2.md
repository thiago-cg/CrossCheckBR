# Review 2 — o que o sistema novo ainda erra, e por quê

Data: 2026-09-30 · Código: `main` @ `17fc2bd` (PR #4 mergeado) · Método: 3 revisores (Opus) em paralelo,
**sem nenhuma chamada paga**: só traces gravados (`runs/`), cassetes (`eval/cassettes/`), replay e sondas offline.
Os achados de maior peso foram reconferidos no código.

| Relatório | Tema |
|---|---|
| [review2/r_erros.md](review2/r_erros.md) | os 50 erros do dev, caso a caso, com simulação offline de regras de decisão |
| [review2/r_codigo.md](review2/r_codigo.md) | bugs de correção na branch mergeada + teste de mutação + variantes em replay |
| [review2/r_eval.md](review2/r_eval.md) | validade das métricas, rótulos, ICs, vazamento, protocolo de eval barato |

---

## 1. Resumo

1. **A métrica atual não distingue o sistema de um chute.** "Sempre alta" acerta 58/70 (83%) no dev; o sistema
   acerta 20/70 (29%, IC 19–40%). "Sempre média" tem **zero** erro grave, e o `--gate` aprovaria isso. Acurácia
   balanceada: sistema 0,39 × "sempre alta" 0,50. O único sinal informativo hoje é `baixa` (4/7 verdadeiros com
   prevalência de 11%) e a AUC ordinal de 0,78 — ambos apoiados em só 8 verdadeiros.
2. **O gargalo é a busca, não o juiz.** A checagem-gabarito apareceu nos resultados em só 4/70 casos, e o juiz
   nunca descartou uma checagem real. A busca `site:` das 13 agências em OR devolveu 3 resultados do tema em 377
   (0,8%) e mesmo assim ocupou 17% das vagas do juiz, por causa do bônus de fonte curada.
3. **Há bugs de decisão que criam erro grave**, confirmados por sonda e replay: o selo de uma página que o juiz
   marcou RELATA_SEM_ENDOSSO (= trata de *outra* alegação) ainda vota; a moldura "É falso que…" é aplicada ao item 0
   mesmo quando a negação está em outra frase; a combinação entre afirmações deixa a parte verdadeira decidir.
4. **2 dos 3 erros graves vêm da reescrita do caso**, não do sistema (vorcaro, ibge). Erro grave real: 1/70.
   Taxa estimada de rótulos errados/contestáveis: 7,8% (IC 3,6–16%).
5. **A melhora 4→0 erros graves da Iteração 1b não é estatisticamente significativa** (McNemar p=0,125, n=12).
   O que se pode afirmar é mecanístico (visto nos traces), não numérico.

## 2. Achados novos, por severidade

### Crítico — decisão (geram erro grave; custo zero para testar)

| # | Achado | Onde | Evidência |
|---|---|---|---|
| D1 | Selo de página RELATA_SEM_ENDOSSO vota | `decisao.py:53` (`CLASSES_TRATA`), `:207-217` | ClaimReview de **template** do SBT News (todo artigo do site tem) virou selo VERDADEIRO (−1,2) → erro grave `cr_jn_soltura_vorcaro`. Replay com a correção: 3→2 graves, acerto igual. O teste `test_selo_verdadeiro_do_fato_ou_fake_baixa` trava o comportamento errado |
| D2 | Selo aceito de qualquer publisher com JSON-LD ClaimReview | `jsonld.py`, `decisao.py` | mesmo caso SBT: selo só deveria valer para checador curado (R8) |
| D3 | Moldura "É falso que…" sempre no item 0 | `afirmacoes.py:184-193` | afirmação verdadeira em outra frase recebe polaridade `nega` → sinal invertido |
| D4 | Combinação entre afirmações | `decisao.py:282-285` | se a parte falsa fica abaixo de τ (ENGANOSO sozinho = 1,05), a parte verdadeira decide → baixa. Mutação "sempre max\|L\|" ou "soma" **não quebra nenhum teste** |
| D5 | Rede social julgada só pelo título vira confirmação | `decisao.py` (F_SO_TITULO), juiz | 3 posts Instagram/Facebook/TikTok que espalham o boato deram SUSTENTA (−1,26) → o único erro grave real (`cr_chico_cesar`) |

### Alto — busca (maior impacto em acerto; exige record)

| # | Achado | Evidência |
|---|---|---|
| B1 | Busca `site:` com 13 agências em OR não funciona | 3/377 resultados do tema; 164 homes/listagens; 17% das vagas do juiz |
| B2 | Consulta ruim ou ruído do Google | 7 casos casando palavra solta ("Porto Seguro" para comício em Porto Alegre); 5 casos perderam a entidade decisiva |
| B3 | Reformulação com "checagem/boato/é falso" rende pior | 10 das 16 buscas com zero resultados do tema (plausível, amostra pequena) |
| B4 | Autor da fala apagado | regra 3 do prompt (`afirmacoes.py:144`) transforma "Fulano disse X" em X: 10 casos; em 3, a negação dentro da fala citada foi lida como negação do usuário |
| B5 | Crawl desperdiçado | Instagram/Facebook: 0 corpos em 264 tentativas; PDFs: 0/66 (decodificação sempre UTF-8, sem olhar `content-type`, também quebra latin-1) |
| B6 | Listagens de agência não seguidas | 75 páginas de listagem chegaram ao juiz com o título da checagem no snippet, mas o link não é seguido |

### Alto — independência e peso de fonte

| # | Achado | Onde | Evidência |
|---|---|---|---|
| I1 | Crédito de foto "Folhapress" junta 4 veículos num cluster | `corroboracao.py:122,131,198-205` | 5 fontes independentes → 2 clusters |
| I2 | Mesma agência em 2 domínios = 2 votos | idem | Lupa em `agencialupa.org` + `lupa.uol.com.br` basta para alta |
| I3 | "Curada" por sufixo de host | `catalogo.py:183` | `comentarios1.folha.uol.com.br` valeu −1,0 como a Folha; blogs em `blogosfera.uol.com.br` herdam peso cheio |
| I4 | Agência sozinha nunca chega a alta | `decisao.py` | 1 agência REFUTA = +1,0 < τ=1,10; 5 casos tinham agência refutando e ficaram em média |

### Médio — juiz e harness

- **Corte duplo do trecho** apaga a conclusão da checagem: `aprofundar` corta em 3000, o juiz recorta de novo em
  2500 por sobreposição de termos; na sonda, "Conclusão: … é FALSO" não chega ao juiz.
- **Verificação de citação** tolera formatação (bom), mas aceita colagem "A … B" de pontos distantes e não detecta
  classe errada (SUSTENTA citando o boato numa página de checagem passa com 1,0).
- **Uma classe fora do enum** invalida o lote inteiro do juiz sem emitir fallback.
- **deepseek-v4.1-flash esgota `max_tokens` em raciocínio**: 9 cassetes com `finish_reason=length` e conteúdo
  vazio; o retry repete a falha e o record congela o 200 vazio; 27 chamadas de juiz acabaram servidas pelo luna.
- **Timeouts**: `LLM_TIMEOUT_S=240` sobrepõe `OPENROUTER_TIMEOUT_S=30` (× 2 tentativas × 3 modelos);
  `JUIZ_TIMEOUT_TOTAL_S` = timeout do caso, então nunca dispara antes; `JUIZ_CONCORRENCIA` não é usado.
  O juiz consome 38% da latência.
- **Crítico com critério diferente da decisão**: 32% das paradas por "≥2 clusters" terminaram em média.
- **Replay não determinístico**: a ordem dos lotes do juiz depende da conclusão assíncrona das páginas (1/70 caso
  diverge); trocar 1 caractere do prompt leva a `http_miss` 2→40 e os casos viram indeterminada silenciosamente.
- **Cota da SerpAPI esgotada não interrompe o eval** (`serpapi_layer.py:346-351` engole), contra a regra do CLAUDE.md.
- **"Parcial" do harness** inconsistente com o README (falso→média nunca conta; enganoso→média conta pleno).

## 3. Simulações já feitas (custo zero)

Reexecutar `decidir()` sobre os julgamentos gravados reproduz 69/69 níveis em <1 s.

| Variante (dev, 70) | Acerto | Erro grave | Indet. | Nota |
|---|---|---|---|---|
| Base (Iteração 2) | 20 | 3 | 27 | |
| D1: RELATA não aplica selo | 20 | 2 | 27 | sem efeito colateral |
| D1 + rede social não pode SUSTENTA | 21 | 1 | 34 | assimétrica: validar no holdout |
| D1 + rede social não vota | 15 | 2 | 38 | **cria erro grave novo** (bets): descartar |
| R1+R11+R2b+R8+VAGO (r_erros) | 26 | 1 | 26 | desenhadas olhando o dev: validar no holdout |
| Limiar τ = ln 2 sozinho | — | +1 | — | **cria erro grave** (cucurella): descartar |

Regras da última linha: R1 = REFUTA de agência curada vale como selo; R11 = se agência refuta, SUSTENTA de
não-agência não vota; R2b = todas as SUSTENTA de rede social contam como um cluster; R8 = selo só de checador
curado; VAGO = trava de sujeito vago só para sujeito genérico.

## 4. Hipóteses ranqueadas

**Nível 0 — grátis (eval de decisão offline, segundos):**

| # | Hipótese | Ataca | Impacto estimado |
|---|---|---|---|
| 1 | D1 + D2/R8: selo só vale se o juiz disse SUSTENTA/REFUTA **e** o publisher é checador curado | D1, D2 | −1 grave |
| 2 | D5/R2b: SUSTENTA de rede social só pelo título = 1 cluster (ou não vota a favor) | D5 | −1 grave |
| 3 | I4/R1+R11: REFUTA de agência curada vale como selo; anula SUSTENTA de não-agência | F (17 casos em média) | +4–6 acertos |
| 4 | D4: combinação entre afirmações — pior caso se qualquer afirmação tem selo/REFUTA de agência | composta | caso bets |
| 5 | I1–I3: agência por assinatura de texto (não crédito de foto); aliases colapsam no mesmo cluster; curada = host exato ou prefixo declarado | independência | pequeno, mas corrige votos inflados |
| 6 | Trava VAGO só para sujeito genérico | ibuprofeno_piora | +1 |

**Nível 1 — grátis (replay), exige mudança de código fora do `decidir()`:** D3 (moldura por frase),
corte único do trecho preservando a conclusão, enum do juiz tolerante, `max_tokens`/`reasoning` do deepseek,
timeouts (juiz 45–60 s por chamada, lotes em paralelo com `JUIZ_CONCORRENCIA`; SerpAPI 10 s + 1 retry),
ordenação determinística dos lotes do juiz, cota SerpAPI interrompendo o eval.

**Nível 3 — exige record (~50 buscas numa subamostra fixa de 20 casos):**

| # | Hipótese | Ataca |
|---|---|---|
| 7 | Trocar `site:` em OR por `site:` de um domínio por vez nas 3–4 agências mais relevantes, ou pela busca interna de cada site; filtrar homes/listagens antes do juiz | B1 (maior efeito esperado) |
| 8 | Manter o autor da fala na afirmação ("Fulano disse X" é o que se checa) | B4 |
| 9 | Não crawlear redes sociais nem PDFs; seguir o link da checagem citada numa listagem | B5, B6 |
| 10 | Reformulação sem "checagem"/"é falso" e sem inventar entidades | B3 |
| 11 | H1 (veredito pelo título dos resultados) — só rende depois da #7 | B1 |

## 5. Mudanças no eval antes de qualquer nova iteração paga

1. **Métricas que batem o trivial**: acurácia balanceada por rótulo, erro grave por direção (falso→baixa e
   verdadeiro→alta separados), cobertura (1 − indeterminada) com precisão por nível, AUC ordinal, e **linhas de
   referência "sempre alta" e "sempre média" impressas em todo relatório**. O `--gate` deve exigir acurácia
   balanceada ≥ a de "sempre alta".
2. **Eval de decisão (nível 0)**: emitir um evento `evidencias` antes do `decidir()` e reexecutar só a decisão
   sobre um snapshot fixo de run_ids. É onde as hipóteses 1–6 devem ser testadas.
3. **Replay confiável**: ordenar os lotes do juiz por URL; um miss de LLM em replay marca o caso como
   "não reproduzido" (fora do delta), em vez de virar indeterminada.
4. **Dados**: corrigir `cr_ibge_trafico_no_pib` e `cr_jn_soltura_vorcaro` com justificativa na `nota`; separar
   `sem_evidencia` em "vago" e "boato sem prova"; chegar a **≥30 verdadeiros** no dev (hoje 8) com Agência Brasil,
   IBGE e BCB; acrescentar casos do tipo `link` (hoje zero). O r_eval.md tem 21 casos propostos.
5. **Vazamento**: registrar a data do snapshot da busca; criar o split `sem_checagem` (filtra domínios de agência
   em replay) para medir checagem cruzada genuína, e não "achar a checagem".
6. **Custo**: iterar na subamostra estratificada fixa de 20 casos (listada no r_eval.md); dev completo e holdout só
   em marcos, sempre com IC e McNemar contra a iteração anterior.

## 6. Precisa de decisão humana

- Política para `sem_evidencia`: `alta` para "boato sem prova" é erro ou acerto?
- Correção dos 2 rótulos (ibge, vorcaro) e revisão da tabela `selos.json` (63/77 rótulos vêm dela).
- Se a regra assimétrica "rede social não pode SUSTENTA" é aceitável como política editorial.
- Incluir a AFP Checamos no catálogo como agência curada.
