# Code review: correção do código (HEAD `c35f7a1`, branch `loop-14` já mergeada na main)

Escopo: `decisao.py`, `juiz_llm.py`/`llm.py`, `pipeline.py`/`agente.py`, `corroboracao.py`, `afirmacoes.py`, `extracao`/`jsonld`/`selos`, `replay.py` e o teste de mutação. Todas as sondas rodaram offline ou em replay, sem nenhuma chamada paga. Scripts em `scratchpad/review2/`: `sonda_*.py`, `mutar.py`, `eval_replay.py`, `sonda_variante*.py`.

Não repito o que já está em `r_erros.md` e `r_eval.md`: SBT como dado, redes sociais como confirmação, regra 3 do prompt de afirmações, timeouts do juiz, trava VAGO, `site:` em OR, listagens, gate, "parcial" e determinismo do replay. Quando um achado meu é a **causa no código** de um sintoma que eles já registraram, digo isso.

`[C]` = confirmado por sonda · `[P]` = plausível, só pela leitura do código.

Linha de base em replay (`eval_replay.py`, dev, 70 casos): acerto 0,286; erro grave 3; indeterminada 0,386; `http_miss` 54. Reproduz a Iteração 2.

---

## 1. Achados por severidade

### ALTO-1 [C] `decidir()` aplica o selo de uma página que o juiz marcou RELATA_SEM_ENDOSSO, mas o prompt manda usar RELATA justamente quando a alegação checada é outra
- **Onde:** `decisao.py:53` (`CLASSES_TRATA` inclui RELATA) e `decisao.py:207-217`. Do lado do juiz, `juiz_llm.py:179` (regra 2: "outra vacina, outra data ou outro lugar não é a mesma afirmação → RELATA") e `:181` (regra 4: considere o selo "só se essa alegação for a mesma"). O crítico repete o mesmo erro em `agente.py:356`.
- **Evidência:**
  - Sintético (`sonda_decisao.py` S2): página do Fato ou Fake que checou *outra data*, classe RELATA, selo FALSO → **alta** (L = +1,5). Com uma afirmação verdadeira, isso vira erro grave verdadeiro→alta.
  - Real: os 5 votos com selo em todos os traces são `RELATA + VERDADEIRO`. Em `cr_jn_soltura_vorcaro` esse voto vale −1,2 e é o maior da decisão (L = −2,02 → baixa, erro grave).
  - Variante V1 (RELATA não aplica selo, via monkeypatch, replay nos 70 casos): erro grave **3→2**, acerto igual (0,286). Vorcaro vai de baixa para média. `lula_nao_acredita_em_deus` perde um acerto que era acidental (alta→média).
- **Mecanismo:** o juiz diz "trata de algo parecido", e a decisão lê isso como "a checagem vale para esta afirmação". O template do SBT (r_erros #3) só causou dano por esta porta.
- **Correção:** o selo vota só com SUSTENTA/REFUTA. Melhor ainda: acrescentar ao schema do juiz um campo `selo_aplica: bool` ("a alegação checada é a mesma?") e exigir `True`. Atualizar `test_selo_verdadeiro_do_fato_ou_fake_baixa`, que hoje trava o comportamento errado com RELATA.
- **Trade-off:** checagens que o juiz marca RELATA por cautela deixam de pesar. r_erros 2.3 mostra que isso é raro.

### ALTO-2 [C] A moldura "É falso que…" é aplicada ao item 0 do LLM, que pode ser outra afirmação: inverte a polaridade de uma afirmação verdadeira
- **Onde:** `afirmacoes.py:184-193`. `moldura = _moldura_negacao(texto)` casa o texto inteiro com `(.+)$` e `re.S`, e é aplicada sempre em `i == 0`.
- **Evidência** (`sonda_afirmacoes.py`, LLM falso):
  - Entrada: "É falso que a vacina altera o DNA. O governo comprou 10 milhões de doses da Pfizer em 2021."
  - O LLM devolve o item 0 = compra de doses (afirma) e o item 1 = vacina (nega).
  - Saída do item 0: `[nega]`, núcleo "A vacina altera o DNA. O governo comprou 10 milhões…", e o texto passa a ser a entrada inteira.
  - Resultado: a afirmação verdadeira ganha `s = −1`. SUSTENTA vira alta, ou seja, erro grave. O item 1 fica duplicado.
- **Correção:**
  - Cortar a moldura na primeira fronteira de frase.
  - Aplicá-la ao item cujo `nucleo`/`afirmacao` tem a maior sobreposição com a moldura, e só se houver sobreposição (Jaccard ≥ 0,5). Nunca pelo índice.
  - Não sobrescrever `af` com o texto inteiro.
- **Trade-off:** nenhum relevante.

### ALTO-3 [C] Combinação entre afirmações: a parte verdadeira de uma afirmação composta decide o nível sempre que a parte falsa fica abaixo de τ
- **Onde:** `decisao.py:282-285`: `L = mx if mx >= TAU else max(|L|)`.
- **Evidência:**
  - S10: af0 com 1 refutação curada (+1,0) e af1 com 2 SUSTENTA → **baixa** (L = −2).
  - S3b: af1 com selo ENGANOSO (+1,05, abaixo de τ = 1,0986) → **baixa**.
  - Com um selo ENGANOSO sozinho nunca chegando a τ (r_erros #5), toda afirmação "X verdadeiro, mas Y enganoso" cai em baixa quando X tem boa cobertura. É o mecanismo de `lula_acabar_bets`, que já foi erro grave e na V2 volta a ser (alta→baixa).
- **Mutação:** trocar a regra por "sempre max|L|" ou por "soma" **não quebra nenhum teste**. A combinação não tem teste.
- **Correção:**
  - Se alguma afirmação tem L > 0 com pelo menos um voto de fonte curada, o nível não pode ser baixa. Use `max(L)` limitado a média.
  - Ou decidir por afirmação e reportar a pior.
  - Ou julgar a entrada original inteira como "afirmação 0", como propõe o ITERACOES.
  - Acrescentar testes de 2 afirmações.
- **Trade-off:** mais "média" em entradas mistas. É a leitura honesta.

### ALTO-4 [C] Clusters de independência: um crédito de foto "Folhapress" funde veículos independentes, e a mesma agência em 2 domínios conta como 2 votos
- **Onde:**
  - `corroboracao.py:122,131`: Folhapress e Estadão Conteúdo são "sempre crédito" e procurados no **corpo inteiro**.
  - `corroboracao.py:198-205`: union-find transitivo.
  - `corroboracao.py:59-75`: cluster por domínio-base.
- **Evidência** (`sonda_clusters.py`):
  - Uma matéria do Estadão com "Foto: …/Folhapress" entra no cluster `agencia:folhapress`, que por transitividade de `uol.com.br` também contém a Folha, a **Lupa (lupa.uol.com.br)** e o **UOL Confere**. São 2 clusters para 5 fontes independentes.
  - Na direção oposta, `agencialupa.org` e `lupa.uol.com.br` (a mesma agência, com alias no catálogo) ficam em clusters diferentes. Em S6, 2 páginas da Lupa → **alta** (L = +2).
- **Correção:**
  - Assinatura de agência só nas janelas de início e fim e só com padrão de crédito de texto, excluindo "Foto:", "Imagem:" e créditos de imagem.
  - Cluster por **`portal_id` do catálogo** (que já resolve aliases) antes do domínio-base.
  - Não usar `uol.com.br`/`globo.com` como grupo quando o portal do catálogo difere (Lupa ≠ UOL Confere ≠ Folha).
- **Trade-off:** as regras por grupo econômico passam a depender do catálogo.

### ALTO-5 [C] "Curada" por sufixo de host: páginas de comentários e blogs hospedados herdam peso cheio
- **Onde:** `catalogo.py:183` (`host.endswith("." + h)`), consumido por `pipeline.py:174`.
- **Evidência:**
  - `comentarios1.folha.uol.com.br/comentarios/…` → `folha`, curada = True. No trace de `lula_acabar_bets` (`…095529-cb8d9f`) esse voto vale −1,00 como se fosse a Folha.
  - `blogdofulano.blogosfera.uol.com.br` → `uol`, curada = True.
- **Correção:** lista de subdomínios e caminhos excluídos (`comentarios*`, `blogosfera`, `/blogs/`, `/colunistas/`) e peso de curada só no match exato de host ou alias.
- **Trade-off:** colunas de veículos curados passam a pesar menos.

### MÉDIO-1 [C] O corte duplo do trecho descarta a conclusão da checagem antes do juiz
- **Onde:** `aprofundar.montar_trecho` (≤ 3000, prioriza `_MARCADOR_CONCLUSAO`), seguido de `juiz_llm.montar_trecho` (`JUIZ_TRECHO_MAX = 2500`, só sobreposição de termos, `juiz_llm.py:67-85`). O pipeline passa `trecho_juiz` para o segundo corte em `pipeline.py:530,541`.
- **Evidência** (`sonda_trecho.py`): o corpo de 5,5 k tem "Conclusão: o conteúdo verificado é FALSO" no fim. O 1º corte mantém a conclusão (2926 chars). O 2º a remove (2430 chars, `conclusão presente: False`).
- **Correção:** se `trecho_juiz` já existe e cabe em ~3000, não recortar de novo. Ou alinhar `JUIZ_TRECHO_MAX ≥ TRECHO_MAX_CHARS` e dar ao 2º corte o mesmo bônus de conclusão.
- **Trade-off:** cerca de 500 chars a mais por item.

### MÉDIO-2 [P] O `trecho_juiz` é um só por página, montado para a 1ª afirmação que a pediu
- **Onde:** `pipeline.py:448` (`_afirmacao` = a do 1º par) e `:468`. `_julgar` reusa `p["trecho_juiz"]` para todas as afirmações (`:530`).
- **Efeito:** a página compartilhada por af0 e af1 é julgada para af1 com os parágrafos escolhidos para af0. O resultado é RELATA/NAO_TRATA a mais para af1.
- **Correção:** guardar `corpo` completo e montar o trecho por (página, afirmação) em `_julgar` a partir de `p["corpo"]`.

### MÉDIO-3 [C] A verificação de citação protege contra citação inventada, não contra classe errada, e aceita colagem
- **Onde:** `juiz_llm.py:88-123`. **Evidência** (`sonda_juiz.py`):
  - **Funciona bem:** aspas tipográficas, maiúsculas, quebra de linha e o "…" que o próprio `montar_trecho` insere dão 1,0 (sem falso negativo). "30 mil" contra "30.000" dá 0,95. Paráfrase de 1 palavra dá 0,984. Duas palavras trocadas dão 0,881 (rebaixa).
  - **Colagem:** "É falso que a vacina ... entra no núcleo da célula" dá **1,0**. Cada pedaço com ≥ 3 palavras é conferido isoladamente, em qualquer posição do texto, e a regra 6 do prompt (sem reticências) não é imposta.
  - **Citação do boato:** um SUSTENTA que cita o próprio boato dentro de uma checagem ("a vacina da Pfizer altera o DNA…") dá **1,0**. A verificação não detecta o erro mais caro, que é SUSTENTA numa página que desmente.
  - Citação com < 3 palavras dá 0, e a página é rebaixada.
- **Correção:**
  - Rejeitar citação com reticências, já que o prompt proíbe (ou exigir que os pedaços estejam a ≤ 300 chars um do outro e em ordem).
  - Para SUSTENTA em página com `tipo_fonte = checagem` ou com selo FALSO/ENGANOSO, exigir que a citação não esteja a menos de ~200 chars de marcadores de desmentido ("é falso", "boato", "#FAKE"). Se estiver, rebaixar para RELATA e registrar `fallback`.

### MÉDIO-4 [C] Uma classe fora do enum derruba o lote inteiro do juiz
- **Onde:** `juiz_llm.py:131-144`. O validador normaliza, mas deixa passar qualquer string, e o `Literal` reprova a resposta inteira.
- **Evidência:** `{"classe": "INCONCLUSIVO"}` ou `"SUSTENTA_PARCIALMENTE"` em 1 dos 3 itens → `ValidationError` no lote. Depois do retry (se repetir), os 5 itens voltam `classe = None`.
- **Correção:** no `field_validator`, mapear o que não conhece para `None`, que vira "juiz não devolveu este item" (`motor = fallback-juiz-item`), e emitir `telemetria.fallback("juiz.item", …)`. Hoje esse caminho também não emite fallback (`juiz_llm.py:239`).

### MÉDIO-5 [C] Raciocínio consome `max_tokens`, e o "retry de resposta vazia" repete uma falha determinística que o record congela
- **Onde:** `llm_openrouter.py:95-108` (retry no mesmo modelo quando a resposta vem vazia) e `replay.py:427-440,463` (record reusa o cassete existente e grava 200 vazio).
- **Evidência:**
  - 9 cassetes do `deepseek-v4.1-flash` (juiz) com `finish_reason = length`, `completion_tokens = 4000`, `reasoning_tokens = 4000` e conteúdo vazio.
  - No replay dos 70 casos aparecem 48 respostas LLM vazias servidas do cassete. O "retry" lê o mesmo cassete, e o juiz cai para o `gpt-6-luna`: 27 chamadas de juiz servidas por outro modelo na "iteração deepseek".
- **Correção:**
  - Ler `finish_reason`. Com `length` e conteúdo vazio, não repetir: subir `max_tokens` ou mandar `reasoning: {"max_tokens": N}`/`exclude`.
  - Não gravar cassete de 200 com conteúdo vazio (tratar como transitório, igual a 429/5xx).
  - Registrar `modelo_efetivo` do juiz por item e reportar no eval a fração julgada por modelo ≠ `OPENROUTER_MODEL_JUIZ`.

### MÉDIO-6 [C] Replay: 1 caractere no prompt vira "indeterminada" e esconde erros graves
- **Onde:** `replay.chave` (o hash do corpo inteiro) e `llm.chat_json`: em replay, o `ReplayMiss` do OpenRouter cai para o local, que também dá miss, e o juiz fica `None`.
- **Evidência** (`sonda_replay_prompt.py`): trocar "." por "!" no `INSTRUCAO` do juiz leva 5/5 casos a **indeterminada**, com `http_miss` indo de 2 para 40. Os 2 erros graves (vorcaro, ibge) "somem". Complementa r_eval A5.
- **Correção:** em replay, `ReplayMiss` de LLM deve marcar o caso como `nao_reproduzido` (fora das métricas e do delta) ou abortar. Hoje ele entra como resultado legítimo.

### MÉDIO-7 [C] O critério de parada do crítico não é o critério da decisão
- **Onde:** `agente.py:367-370` ("≥ 2 clusters com postura" ou "selo com direção").
- **Evidência:**
  - No replay (70 casos), o crítico parou por "≥ 2 clusters" em 31 afirmações, e em **10 (32%)** o |L| final ficou abaixo de τ, ou seja, média.
  - Dois clusters opostos, ou dois não curados só com título (0,42 × 2 = 0,84), bastam para parar.
  - Das 50 afirmações que ganharam onda extra, só 5 terminaram com |L| ≥ τ.
- **Correção:** o crítico chama `decisao.decidir` sobre as evidências parciais daquela afirmação e para se `|L_a| ≥ τ`. Uma única fonte de verdade.

### MÉDIO-8 [C] Selo principal de página com várias ClaimReview é escolhido às cegas
- **Onde:** `jsonld.py:209` (o primeiro com veredito) e `pipeline.py:475-481` (usa `vp["veredito"]` e ignora `claims`). A docstring diz "o chamador deve casar a afirmação certa", mas o chamador não faz isso.
- **Efeito:** checagem de debate ou discurso com 10 alegações aplica o selo da 1ª a qualquer afirmação. O juiz vê só a `alegacao_checada` principal.
- **Correção:** escolher em `claims` a de maior sobreposição com o núcleo (mínimo de 0,5). Sem match, sem selo.
- Ligado a isso: exigir `tipo_portal == "checagem"` para usar ClaimReview da página. Isso resolve o SBT pela raiz, não por lista negra.

### MÉDIO-9 [C] PDFs e páginas latin-1: conteúdo perdido ou corrompido em silêncio
- **Onde:** `aprofundar.py:256`: `r.content.decode("utf-8", errors="ignore")`, sem olhar `content-type`/`charset`.
- **Evidência:**
  - 73 cassetes `application/pdf` com 0/20 extraídos (avisos "trafilatura: sem texto", "regex: curto"). Viram "corpo curto/JS/paywall", não "pdf".
  - 14 páginas ISO-8859-1: "Um cidado est devendo…", "Caf da manh… mobilizao". O juiz recebe texto sem acento, e os termos do trecho e do BM25 não casam.
- **Correção:**
  - Decodificar pelo `charset` do header ou do `<meta>` (httpx `r.encoding` ou `charset_normalizer`).
  - PDF: marcar `metodo = "pdf"` e extrair com `pypdf` (primeiras páginas) ou pular explicitamente com motivo "pdf".
- r_erros #8 sugere não crawlear PDF de repositório. Nota do Oreo da Lupa e diários oficiais são fonte primária, então valeria extrair.

### MÉDIO-10 [P] O timeout do juiz descarta tudo, inclusive afirmações já julgadas, e as threads continuam gastando
- **Onde:** `pipeline.py:549-554` (`resultados = {}` no `TimeoutError`) e `agente.py:235-239` / `aprofundar.py:306-310` (`cancel` em `to_thread` não para a thread).
- **Efeito:**
  - Uma afirmação lenta anula o julgamento das outras.
  - Chamadas LLM e SerpAPI seguem depois do timeout (custo) e escrevem eventos num run já finalizado.
  - O eval usa `--timeout-caso 300`, menor que `JUIZ_TIMEOUT_TOTAL_S 600`.
- **Correção:** acumular por afirmação ou lote num dict compartilhado e, no timeout, usar o que já voltou. Passar um `threading.Event` de cancelamento para `julgar` checar entre lotes.

### MÉDIO-11 [C] Cota da SerpAPI esgotada não interrompe o eval
- **Onde:** `serpapi_layer.py:346-351` engole `CotaSerpAPIEsgotada` e `OrcamentoSerpAPIEsgotado` e devolve `erro`. `eval/run.py` não tem circuit breaker, e o CLAUDE.md pede "pare e avise".
- **Efeito:** foi o que invalidou a iter1a (138 buscas recusadas) e a iter2. `_reservar_serpapi` conta cada recusa como uso live.
- **Correção:** flag de processo `replay.cota_esgotada` e, em `executar_casos`, parar de iniciar casos, marcar os restantes como `nao_rodado` e sair com código ≠ 0.

### BAIXO
- **[C] JSON truncado aceito em silêncio.** `llm.extrair_json` (`llm.py:151-158`) encontra o 1º objeto interno de uma resposta truncada. Com n ≤ 2 itens, `_cobertura` aceita 1 item. Correção: exigir `finish_reason != length` e rejeitar dict sem `itens` quando se pediu lista.
- **[C] Consulta "PL das Fake News" vira "PL das News".** A limpeza de palavras de veredito (`afirmacoes.py:211`) apaga "fake" quando ele faz parte de um nome próprio. Correção: só remover se não estiver no núcleo.
- **[C] `why`/justificativa conta pares (afirmação, cluster) como "fontes independentes".** A mesma URL julgada para 2 afirmações vira "2 fontes independentes contestam" (S13). Com várias afirmações, "2 contestam e 3 confirmam" aparece junto com nível alta sem dizer de qual afirmação (S4). Correção: contar URLs distintas e dizer qual afirmação definiu o nível.
- **[C] `_eh_generica` pós-leitura é quase código morto.** Os ramos que dependem de `len(trecho) < 200` nunca disparam para corpo lido (≥ 500). Hubs como `/fato-ou-fake/politica/`, `/tag/vacina` e `/categoria/verificacao` passam (`pipeline.py:61-80`). Causa no código do achado de listagens de r_erros.
- **[P] `fallback` faltando.**
  - Lote do juiz que falha parcialmente: o fallback só dispara se **todos** os itens da afirmação falham (`pipeline.py:562`).
  - `_catalogar` com `except: portal=None` (`pipeline.py:172`).
  - Orçamento do `aprofundar` estourado: só `log.warning`, sem `telemetria.fallback` (`aprofundar.py:305-310`).
- **[C] Descasamentos de peso.**
  - Selo ENGANOSO de agência curada com REFUTA na mesma página = 1,05 → média (S1b). ClaimReview FALSO de site não curado = 1,2 → alta (S1c). A confiança na fonte fica invertida.
  - Postura REFUTA "sem comprovação" vale 1,0, mas o selo SEM_EVIDENCIA da mesma página vale 0,6.
  - A calibração de pesos em si está em r_erros #5.
- **[P] Cache de `aprofundar._cache` em módulo (1 h) guarda falhas** (timeout sob `--paralelo`) e as reaproveita em outros casos do mesmo processo. Introduz dependência de ordem em record/live.

---

## 2. Teste de mutação (`mutar.py`, sobre uma cópia do repo em `scratchpad/review2/mut/repo`)
Linha de base: 319 testes verdes.

| mutação | resultado | teste que pega |
|---|---|---|
| inverter o sinal de REFUTA/SUSTENTA | morta | `test_agente::test_critico…` (e os de decisão) |
| τ = ln 1,5 / ln 5 | morta | `test_mesmo_cluster_conta_um_voto` / `test_selo_verdadeiro…` |
| F_NAO_CURADA 1,0; W_VEREDITO 1,0 | morta | testes de peso e selo |
| RELATA não aplica selo (a **correção** do ALTO-1) | morta | `test_selo_verdadeiro_do_fato_ou_fake_baixa` trava o bug |
| **combinação entre afirmações: max\|L\| ou soma** | **viva** | nenhum |
| cluster soma (sem 1 voto) | morta | `test_mesmo_cluster_conta_um_voto` |
| ignorar polaridade | morta | `test_usuario_nega…` |
| conflito selo×postura somar | morta | `test_selo_e_postura_em_conflito…` |
| **F_SO_TITULO 0,7 → 1,0** | **viva** | nenhum |
| **postura + selo da mesma página somam (a dupla contagem que o D5 corrigiu)** | **viva** | nenhum |
| ENGANOSO 1,0 / SEM_EVIDENCIA 0 (`selos.py`) | morta | só por `test_selos::test_direcao`, que confere as constantes (tautológico) |
| remover travas vago/opinião; contar `motor=fallback-*` | morta | testes de trava |

Os testes de `decidir` são bons para sinal e polaridade. Faltam três:
1. 2 afirmações, com a regra de combinação.
2. Peso "só título".
3. REFUTA + FALSO na mesma página = um voto de 1,5, e não 2,5.

Os testes de selo deveriam verificar o **nível** que resulta de cada selo, não as constantes.

---

## 3. Variantes simuladas em replay (monkeypatch, 70 casos dev, sem rede)

| variante | acerto | erro grave | indeterminada | observação |
|---|---|---|---|---|
| base | 0,286 | 3 | 0,386 | |
| V1: RELATA não aplica selo (ALTO-1) | 0,286 | **2** | 0,386 | vorcaro baixa→média; lula_deus alta→média (acerto acidental) |
| V2: V1 + rede social não vota | 0,214 | 2 | 0,543 | chico corrigido, mas **lula_bets alta→baixa** (novo grave): as refutações vinham de X/IG |
| V3: V1 + rede social não pode **SUSTENTA** (REFUTA mantido) | **0,300** | **1** | 0,486 | chico baixa→indet.; estribo média→alta; perde 4 médias parciais |

A V3 é assimétrica. Quem espalha o boato não confirma, mas um desmentido em rede social ainda conta. Isso pode inflar "alta" em casos verdadeiros, e o dev só tem 8 verdadeiros, então o holdout tem de confirmar antes de aceitar.

---

## 4. Premissas de design questionáveis
1. **"A página afirma X com a própria voz" (SUSTENTA) é tratado como evidência de que X é verdade.** O juiz faz o que o prompt pede: post que espalha o boato recebe SUSTENTA. A falha está em `decidir` somar SUSTENTA de qualquer fonte. Postura (o que a página diz) e confiabilidade (quem é a página) precisam ser eixos separados. SUSTENTA de UGC ou de fonte não curada deveria ter peso 0, ou só valer com corroboração curada.
2. **A verificação de citação como garantia de qualidade do juiz.** Ela garante que o texto existe na página, não que a classe está certa (MÉDIO-3). Em checagens, o boato está sempre no texto.
3. **Selo e postura como votos intercambiáveis, com `max`.** O selo diz respeito a outra alegação (a `claimReviewed`) e só vale se for a mesma. É uma relação que o código hoje não modela (ALTO-1, MÉDIO-8).
4. **Independência por domínio-base.** Grupo econômico ≠ redação. Crédito de foto ≠ republicação (ALTO-4). O `portal_id` do catálogo é o identificador certo.
5. **Crítico com heurística própria.** Qualquer critério de parada diferente de `decidir` gasta busca quando não precisa, ou para cedo (MÉDIO-7).
6. **Replay por hash do prompt inteiro, sem distinguir miss de resultado.** Serve para iterar só em `decidir`. Mudanças em seleção, trecho ou prompt exigem record, e um miss hoje se disfarça de "indeterminada" (MÉDIO-6).

## 5. Novas hipóteses de melhoria (impacto estimado no dev de 70)

| # | mudança | impacto | custo |
|---|---|---|---|
| H-a | ALTO-1 (selo só com SUSTENTA/REFUTA + `selo_aplica`) + ClaimReview só de `tipo_portal=checagem` | −1 grave [C] | baixo, só decisão |
| H-b | Rede social não confirma (V3), validada no holdout | −1 grave, +1 acerto [C no dev] | baixo |
| H-c | Combinação entre afirmações sem baixa quando alguma tem L > 0 curado (ALTO-3) | evita a classe "composta→baixa" (lula_bets, vorcaro) | baixo |
| H-d | Moldura por sobreposição (ALTO-2) + polaridade preservando o verbo de fala | protege os ~16% de casos com atribuição (r_erros #4) | baixo |
| H-e | Trecho sem corte duplo + trecho por afirmação (MÉDIO-1/2) | mais REFUTA em checagens longas | médio, **exige record** (prompt muda) |
| H-f | Crítico usa `decidir` (MÉDIO-7) | menos buscas desperdiçadas; ~10 afirmações/70 ganham onda | baixo, exige record da onda 2 |
| H-g | Clusters por `portal_id` e sem crédito de foto (ALTO-4) | corrige super- e sub-contagem; efeito líquido pequeno no dev | baixo |
| H-h | `finish_reason=length`: não repetir, subir o orçamento de raciocínio; não gravar 200 vazio (MÉDIO-5) | juiz consistente (sem luna misturado) | baixo |
| H-i | Charset + PDF (MÉDIO-9) | fontes primárias legíveis; ~5% das páginas | médio |

Ordem sugerida: H-a, H-c, H-d, H-b, H-h (só decisão ou parsing, dá para validar em replay), depois H-e e H-f com um record pequeno.
