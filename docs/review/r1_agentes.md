# R1 — Revisão do fluxo de agentes do CrossCheckBR (harness, prompts, comunicação, premissas)

Escopo: `factcheck_mvp/{agente,serpapi_layer,pipeline,juiz_llm,llm_openrouter,padroes_llm,afirmacoes,implicacao,config,agregador,corroboracao,catalogo,descoberta_site}.py`, os testes e os logs. Nenhum arquivo do repo foi alterado (`git status` no fim igual ao do início). Não existe `.env`, então tudo foi rodado offline, com fakes no lugar de SerpAPI, LLM e deep crawl.

## Como verifiquei

- `python3 -m pytest tests -q` → **90 passed em 0.32s**. Todos herméticos (ver achado 12).
- Sondas no scratchpad (arquivo `.py` e saída `.out` de cada uma):
  - `sonda_agente.py`: crítico, orçamento, dedupe, tamanho da query, contador compartilhado.
  - `sonda_pipeline.py`: pipeline real com SerpAPI, juiz e deep crawl falsos (P1–P5).
  - `sonda_p6.py`: o que chega ao resumidor.
  - `sonda_padroes.py`: ramo local de padrões e parser do juiz.
  - `sonda_afirm.py`: parsing das afirmações.
- **Limite importante da evidência empírica.** Todos os logs da raiz (`run_full*.log`, `run_checagem.log`, `bot.log`, `api_test.log`) são de **21/09**. Isso é antes do juiz (loop 12, merge 23/09) e do agente (commit `d40c812`, 28/09). Os logs mostram o pipeline antigo (google_news + laya + LLM local LFM2.5-VL-3B) e o crawler. **Não existe registro live do agente nem do juiz atual.** `output_ab/` não existe, então também não há evidência local de que o A/B tenha rodado.

## Que modelo cada etapa usa

| Etapa | Onde | Modelo / endpoint | Fallback |
|---|---|---|---|
| Afirmações | `afirmacoes.py:51-63` | OpenRouter chat `deepseek/deepseek-v4-flash` (provider StreamLake) | `gemma-4-26b-a4b-it:free` → Unsloth local (LFM2.5-VL-3B, segundo `run_full.log:1`) → regex |
| Resumo + termômetro (juiz) | `juiz_llm.py:50,114` | mesmo chat | lexical (`implicacao._fallback_lexico`) ou `lexico-top3` |
| Padrões | `padroes_llm.py:56` | mesmo chat | Unsloth local → regex |
| Tipo de site | `descoberta_site.py:333-352` | Jev `~typesafe/jev-latest` (Decisions API) | tipo heurístico |
| Agente de descoberta | `agente.py` | **nenhum LLM**: só regex e regras | — |

Custo e teto: cerca de 16 chamadas de chat por consulta contra `LLM_DAILY_CAP=50` (`config.py:41`). Com isso, a partir da ~3ª consulta do dia **todo** o fluxo passa a rodar em fallback (achado 2).

---

## Achados, do mais grave ao menos grave (para o objetivo do experimento)

### 1. CRÍTICO — Três checadores que REFUTAM a afirmação resultam em propensão BAIXA
- **Onde:** `pipeline.py:563-575` (sinal "cobertura ampla") e `agregador.py:64-70` (`_direcao`: "ampla" sem "falso" vale **-0.5**).
- **Evidência (sonda P1):** afirmação "Ibuprofeno cura dengue". Aos Fatos, E-farsas e Boatos.org entram com corpo lido, e o juiz dá score -90 para os três.
  - Sinais gerados: `fontes refutam (+1, conf .8)`, `cobertura ampla (-0.5, conf .75)` e mock 0.5.
  - Resultado: **`propensão: baixa`**.
  - Agregador isolado (P1b): só "refutam" + mock = **alta (0.714)**; com "cobertura ampla" junto = **baixa (0.227)**.
- **Mecanismo:**
  - `corroboraveis` inclui toda fonte com `relevante is True`, sem olhar a direção.
  - `contar_independentes` conta 3 grupos, então sai "cobertura ampla".
  - O agregador interpreta isso como "a informação está em 3 veículos", ou seja, confiável.
  - Resultado: cobertura do **tema** é tratada como confirmação da **afirmação**. A refutação anula a si mesma.
- **Correção:** calcular a corroboração por direção.
  - `n_sus_indep` = grupos independentes com `sustenta>=0.6`; `n_ref_indep`, idem para refuta.
  - Emitir "cobertura ampla sustenta" só se `n_sus_indep>=3`, e "refutação convergente" se `n_ref_indep>=2`.
  - Remover o default `-0.5` de `_direcao` para "ampla" sem direção.
- **Trade-off:** menos sinais "baixa" (mais `indeterminada`) enquanto o juiz for ruidoso. É o comportamento certo para um checador.

### 2. CRÍTICO — O fallback `lexico-top3` inventa relevância e a etapa continua dizendo "julgamento llm-juiz"
- **Onde:** `pipeline.py:459-472` e o texto da etapa em `pipeline.py:538-543`.
- **Evidência (sonda P3):**
  - LLM indisponível; é o que acontece com cap estourado, sem chave, 429 ou timeout de 120s em `pipeline.py:451-455`.
  - A SerpAPI devolve 3 páginas catalogadas **fora do tema**: "Dólar fecha em alta", "Final da Copa do Brasil", "Eleições nos EUA".
  - Saída: `cobertura ampla`, **`propensão: baixa`**, etapa `"3 relevante(s) após julgamento llm-juiz (3 com corpo lido…)"`.
- **Mecanismo:**
  - Com `juiz_ok=False`, as 3 primeiras catalogadas (ordenadas por overlap, mesmo que o overlap seja 0) recebem `{"sustenta":0.35,"relevante":True}` sem ler o conteúdo.
  - `relevante True` + corpo lido deixa `tem_corpo=True`, o que desliga a contenção em indeterminada (`pipeline.py:634`) e alimenta o achado 1.
  - Com `LLM_DAILY_CAP=50`, esse é o modo **normal** depois de ~3 consultas por dia.
  - A limitação "Sem LLM-juiz…" aparece, mas o veredito sai assim mesmo e o recibo afirma que houve julgamento. É exatamente o "fallback silencioso que mascara que o agente não rodou".
- **Correção:**
  - Apagar o bloco `lexico-top3`.
  - Sem juiz, `relevante=None` (não julgado). Não conta para corroboração e força `indeterminada`.
  - O texto da etapa deve usar o motor real (`llm-juiz` / `lexico` / `nenhum`) e a contagem por motor.
  - Opcional: `status="parcial"` sempre que `juiz_ok=False`.
- **Trade-off:** com o cap atual, a maioria das respostas vira `indeterminada`. Isso expõe o problema real (orçamento de LLM) em vez de escondê-lo.

### 3. ALTO — Prompt do juiz: `relevante` nunca é definido, o exemplo traz `true` literal, e o flag passa por cima do score
- **Onde:** `juiz_llm.py:33-38` (`_JUIZ_INSTRUCAO`) e `juiz_llm.py:86-89`.
- **Evidência:**
  - Prompt literal: `Responda SÓ o JSON… {"0": {"score": N, "relevante": true}, ...}`. Não há uma frase sequer explicando o que "relevante" significa.
  - `_parse_juiz('{"0": {"score": 0, "relevante": true}}')` → `{0: {'score': 0, 'relevante': True}}`.
  - Sonda P2: três fontes com score 0 e `relevante:true` → "cobertura ampla" → **baixa**.
- **Mecanismo:** o score só é usado quando o flag não é bool. Um modelo que copia o exemplo marca tudo como relevante, e fontes neutras viram cobertura (achado 1) e passam a ancorar o corpo (`tem_corpo`).
- **Correção:**
  - Tirar `relevante` da saída do LLM e derivar de `abs(score)>=JUIZ_RELEVANCIA_MIN`, ou pedir um rótulo enum `{SUSTENTA, REFUTA, MENCIONA_SEM_POSICAO, FORA_DO_TEMA}` junto com o score.
  - Exemplo com valores variados (um negativo, um zero com `FORA_DO_TEMA`).
  - `response_format` JSON / json_schema, se o provider suportar.
- **Trade-off:** mais tokens de saída; os parsers e testes que dependem de `relevante` precisam mudar.

### 4. ALTO — O juiz em duas etapas perde a informação que decide
- **Onde:**
  - `pipeline.py:444-445`: `texto_j = trecho or (_snippet or titulo)`.
  - `juiz_llm.py:27-31` (resumo) e `juiz_llm.py:113-116` (o juiz só recebe `[i] resumo`).
  - `pipeline.py:234` (selo do índice).
- **Evidência:**
  - **Sonda P6:** item com título "É falso que ibuprofeno cura dengue" e snippet "Publicado em 12/03/2024 por Redação…". O resumidor recebe **só** `'Publicado em 12/03/2024 por Redação da equipe.'`. O título, que carrega o veredito, é descartado sempre que existe snippet.
  - **Sonda P1:** o juiz recebe `['A checagem diz que…']`. Nada de título, veículo, URL, data, tipo (checagem / notícia / paper do Scholar) ou selo.
  - Para os selos do índice, o campo `veredito` estruturado não chega ao LLM (`pipeline.py:234` envia título + corpo).
- **Mecanismo:**
  - O resumidor recebe a ordem "sem dar veredito" (`juiz_llm.py:29`). Em peça de checagem, o veredito do checador é o fato central, e a instrução ambígua pode apagá-lo (**plausível**, não dá para confirmar sem live).
  - O resumidor também pode reduzir "circula nas redes que X" a "a notícia afirma X", invertendo o sentido epistêmico (**plausível**).
  - `FORA_DO_TEMA` é comparado de forma exata (`juiz_llm.py:100`), então "FORA_DO_TEMA." com ponto vai para o juiz como se fosse resumo.
  - Duas chamadas por peça dobram custo e latência e consomem o cap.
- **Correção:**
  - Uma chamada por peça, ou um lote com itens estruturados: `{titulo, veiculo, tipo_fonte, data, selo_original, trecho}`.
  - Saída: `{stance enum, score, citacao_literal}`.
  - Instrução explícita: "se a peça RELATA que X circula e a desmente, stance=REFUTA".
  - Validar que `citacao_literal` aparece no trecho (grounding barato).
  - Normalizar `FORA_DO_TEMA` com `strip(" .\"'")`.
- **Trade-off:** prompt maior por item; o teto de 10 continua valendo. Metade das chamadas de LLM.

### 5. ALTO — O "agente" não é agente, e o crítico quase nunca age (e quando age, escolhe a pior reformulação)
- **Onde:** `agente.py:122-140` (`_critico`), `agente.py:153` (teto), `agente.py:200-235`, `serpapi_layer.py:30-49` e `78-103`.
- **Evidência:**
  - **S1:** para "Ibuprofeno cura dengue em 3 dias", o filtro `site:` devolve um boatos.org sobre "Lula foi preso?". Decisão: `onda 2 dispensada (checagem ou overlap forte)`. O crítico para quando existe **qualquer** URL de host de checagem, sem checar se ela é do tema.
  - A classificação por host tem falsos positivos: `eh_secao_checagem` dá True para notícia de mercado do valorinveste.globo.com e para página de vídeo do canalsaude.fiocruz.br.
  - E tem falso negativo na Lupa real: `agencialupa.org` fica fora de `SITES_CHECAGEM` e do catálogo (`por_dominio → None`). O `run_full2.log` mostra que a Lupa hoje publica em `www.agencialupa.org/verificacao/...`.
  - **S3:** 3 afirmações (o padrão é `MAX_AFIRMACOES=3`) com temas saúde/quente e `AGENTE_MAX_BUSCAS=8`. As 8 buscas vão todas para a onda 1 e o `teto 8` corta até buscas da própria onda 1. A **onda 2 nunca acontece**.
  - **S2:** `_critico` escolhe a `related_search` com **mais tokens novos** (`agente.py:137-139`). Entre "ibuprofeno dengue é verdade" e "ibuprofeno preço farmácia comprar online barato entrega", fica com a segunda. O critério premia deriva.
  - A docstring (`agente.py:3-5`) promete "laya onde houver"; não existe laya no módulo.
- **Mecanismo:** o crítico decide com base em um proxy (host + overlap de título) em vez do sinal que importa (o juiz achou evidência relevante?). O juiz roda **depois** do agente (`pipeline.py:433+`), então o loop nunca vê o que o juiz achou.
- **Correção:**
  - (a) Reservar orçamento: `teto_por_afirmação = 2 + 1 reservada para onda 2`.
  - (b) Parada apenas com "checagem **e** overlap(afirmação, título) >= 0.5".
  - (c) Escolher a sugestão pela **maior similaridade** com a afirmação entre as que trazem ≥1 token novo, ou gerar uma query de palavras-chave com LLM a partir da afirmação e dos títulos vistos.
  - (d) Idealmente, mover o juiz para dentro do loop (achado de design, ver fim).
  - (e) Adicionar `agencialupa.org` e trocar valorinveste/canalsaude por `secao_apenas` com paths.
- **Trade-off:** +1 busca por afirmação quando a onda 2 dispara; o juiz dentro do loop aumenta a latência.

### 6. ALTO (premissa) — O "caminho feliz" do índice de vereditos está vazio
- **Onde:** `api.py:60-64` carrega `data/amostra_*.json`; `indice.py` usa BM25Plus; o README (etapa 3) promete "80% resolvido".
- **Evidência (sonda inline):**
  - `idx_ver 22`, `idx_not 14`, **1 doc com veredito** (VERDADEIRO sobre golpes, valorinveste; foi o que causou o caso "água da torneira").
  - Os caminhos são `/`, `/about/`, `/sobre/`, `/fato-ou-boato/`, `/estadao-verifica`… Os logs do crawler (`run_full2.log`) mostram a seleção pegando homepages e seções.
  - "Vacina causa autismo" → 5 hits (estadao-verifica, fato-ou-boato…), todos homepages, sem veredito.
  - "Lula foi preso ontem em Brasília" → boatos.org/redes-sociais, e-farsas.com/.
- **Mecanismo:**
  - O BM25Plus dá score positivo por termo presente no corpus, e homepages de 8000 caracteres casam com qualquer assunto.
  - Esses hits viram `fontes` genéricas e ativam "cobertura isolada" (`pipeline.py:576`), porque `fontes` não fica vazia.
  - O teto do juiz é consumido primeiro pelo índice (`pipeline.py:233-236`).
- **Correção:**
  - Trocar a etapa 3 por busca de ClaimReview (Google Fact Check Tools API, `claims:search?languageCode=pt`), que devolve checagem + `textualRating` dos checadores BR que publicam ClaimReview.
  - Ou indexar só URLs de artigo (com path datado) que tenham veredito.
  - Enquanto isso, desligar a etapa no recibo.
- **Trade-off:** a cobertura depende de o checador publicar ClaimReview (a maioria dos grandes publica; plausível, não verifiquei um por um); precisa de uma chave de API gratuita.

### 7. MÉDIO — O catálogo "curado" cresce sozinho, e resultado do Scholar vira "G1"
- **Onde:**
  - `pipeline.py:390-418` (descoberta de catálogo antes do juiz), `descoberta_site.py:355-406` (`inserir_no_catalogo` grava na hora).
  - A docstring em `descoberta_site.py:7-8` diz "Nunca há auto-merge em catalogo.json".
  - `catalogo.py:83`.
- **Evidência:**
  - `git diff catalogo.json`: +207 linhas, catálogo de 20 → **35** portais. Entraram, entre outros, `hnscpm-org-br` (hospital), `difusora95-com-br` (rádio), `bandab`, `folhamax`, `agoralitoral`, `ncnews`.
  - `Catalogo.por_nome_fonte("") → g1`.
  - `normalizar_item(scholar)` sai com `source` vazio, e `rotear_fonte` devolve `{'id':'g1','nome':'G1'} catalogada=True`.
- **Mecanismo:**
  - `nao_cat` vem de `ordenadas`, que foi ordenado **só por overlap lexical, antes do juiz**. Os comentários falam em "relevante", mas nada foi julgado.
  - `catalogada` controla a allow-list do deep crawl e o tier de confiança 0.75 (`pipeline.py:477-501`), então deixa de significar "curado".
  - Em `por_nome_fonte`, `"" in conhecido` é sempre True. Todo resultado de Scholar aparece como G1 e ainda ocupa vaga do `top_crawl`, que falha com "fora do catalogo".
- **Correção:**
  - `if not texto: return None` em `por_nome_fonte`.
  - Descoberta só **depois** do juiz, e só para peças com `relevante is True`.
  - Gravar em `catalogo_propostas.json` (fila), nunca em `catalogo.json`; tier provisório até promoção manual.
- **Trade-off:** menos corpo lido de sites novos por consulta.

### 8. MÉDIO — A mesma evidência é contada duas vezes, e sinais derivados da mesma peça se anulam
- **Onde:** `pipeline.py:204-290` (fontes do índice) + `pipeline.py:514-536` (fontes web), sem dedupe entre os dois; `pipeline.py:572-580`.
- **Evidência:**
  - **P4:** a mesma URL da Aos Fatos está no índice (selo FALSO) e aparece na web. Ela entra **2x** em `fontes`, gera `veredito-existente (+1, .9)` **e** `fontes refutam (+1, .8)` e consome 2 resumos do teto.
  - **P5:** 1 fonte com corpo que sustenta gera "sustentam (corpo lido)" (-1, .75) e "cobertura isolada" (+1, .7). O resultado vira indeterminada.
- **Mecanismo:** não há um conjunto canônico de evidências. Cada etapa soma sinais sobre a mesma peça sem saber o que as outras já contaram.
- **Correção:**
  - Dedupe por URL canônica (sem query nem www, com `final_url` do crawl) entre índice e web **antes** do juiz. Uma peça julgada gera exatamente um registro de evidência.
  - Derivar veredito-existente, sustenta/refuta e cobertura de uma única tabela `evidencias[]`.
  - "Isolada" só quando `n_sus_indep + n_ref_indep == 0`.
- **Trade-off:** refatorar `pipeline.py` (o arquivo tem 677 linhas; é uma boa hora para quebrar em funções).

### 9. MÉDIO — Extração de afirmações e queries: parsing frágil e prompt incompleto
- **Onde:** `afirmacoes.py:14-25`, `afirmacoes.py:58-60`, `agente.py:73-81`, `serpapi_layer.py:141-148`.
- **Evidência:**
  - **sonda_afirm:** para a saída `"Aqui estão as afirmações factuais extraídas:\n1. 30 mil pessoas morreram…\n2. 2026 terá eleição…"`, o código aceita:
    - `'Aqui estão as afirmações factuais extraídas:'` como **afirmação #1**, gastando 1 das 3 vagas e buscas SerpAPI;
    - `'mil pessoas morreram de dengue em 2024…'`, porque `_limpa_linha` (`^[\-\*\d\.\s]+`) come os números que fazem parte da afirmação;
    - `'terá eleição presidencial…'` (perdeu o ano).
  - **S5:** a query "avançada" de uma afirmação de 25 palavras tem **48 palavras** (13 `site:` + 12 `OR`). Isso passa do limite de ~32 termos do Google, e o próprio comentário em `serpapi_layer.py:143-144` admite que "a perna do filtro zera".
  - O filtro inclui hosts amplos (`gov.br`, `g1.globo.com`, `estadao.com.br`, `noticias.uol.com.br`), o que contradiz a docstring em `serpapi_layer.py:134-139` ("só o bloco site: puro nos hosts dedicados funciona").
  - O prompt não pede afirmações autocontidas (pronomes: "Ele foi preso…") nem proíbe acrescentar fatos.
  - Em `bot.log` (pipeline antigo, LLM local 3B) aparecem afirmações como "O artigo 99 da Lei nº 10.406/2002 estabelece a proibição de apostas esportivas". A lei é o Código Civil, então é **plausível** que seja alucinação; não vejo a entrada do usuário para confirmar.
  - A afirmação inteira, com o ponto final, vira a query do Google.
- **Correção:**
  - Saída JSON `[{"afirmacao": "...", "consulta": "3-6 palavras-chave"}]`.
  - `_limpa_linha` só remove `^\s*(\d+[.)]|[-*•])\s+`.
  - Descartar linhas terminadas em ":".
  - Instruções "não acrescente informação ausente do texto" e "resolva pronomes".
  - Na busca, usar `consulta`, não a frase; filtro `site:` só com hosts dedicados, no máximo ~8.
- **Trade-off:** uma dependência a mais do LLM (a consulta). Com fallback: keywords por tokens não-stop.

### 10. MÉDIO — Estado do harness: contagem, atribuição e cortes que desfazem o round-robin
- **Onde:** `agente.py:170-185`, `agente.py:237-247`, `pipeline.py:338-345`, `pipeline.py:373-376`.
- **Evidência:**
  - **S6:** 3 `/checar` concorrentes no mesmo cliente fazem 6 buscas reais, mas cada requisição acredita ter usado `{4, 5, 6}`. `buscas_usadas` é o delta de `cliente.uso_hoje`, que é compartilhado. Se `uso_hoje` falhar (`antes=None`), o delta vira o uso do dia inteiro.
  - **S4:** a mesma URL retornada para duas afirmações fica atribuída só à primeira (`_afirmacao`), e a segunda nunca é julgada contra ela.
  - `pipeline.py:373-376` ordena **globalmente** por overlap e fatia `[:12]`. A afirmação com títulos mais parecidos monopoliza a triagem e o teto do juiz, e o round-robin cuidadoso do agente se perde.
  - Timeout de 60s descarta **tudo** (`unicas=[]`) mesmo que parte das buscas tenha terminado. As buscas do agente são **sequenciais** (até 8 × timeout de 25s), enquanto o caminho estático usa `gather`.
- **Correção:**
  - Contador de busca local ao `descobrir` (incrementar quando `ultimo_motivo` ∈ {ok, vazio} e não for cache-hit; expor o cache-hit no retorno do cliente).
  - Dedupe guardando `_afirmacoes: set`.
  - Triagem e teto do juiz **por afirmação** (`ceil(10/n_afs)`).
  - `descobrir` devolve o parcial via objeto compartilhado.
  - Buscas de uma mesma onda em paralelo.
- **Trade-off:** pouca complexidade extra.

### 11. BAIXO — Padrões: o LLM local responder "NENHUM" produz um sinal de desinformação
- **Onde:** `padroes_llm.py:97-98` (`return []` num ramo que deveria devolver tupla).
- **Evidência (sonda_padroes):**
  - Com o LLM local respondendo `NENHUM`: `{'padroes': [{'padrao': 'sem_data_local', …}], 'motor': 'regex-deterministico'}`.
  - O `ValueError` do unpacking é engolido e o regex acusa "sem data" em todo texto com mais de 300 caracteres. Resultado: `llm-padroes` com +1 e conf .65.
  - O `bot.log` mostra que o caminho local era de fato usado (`POST 127.0.0.1:8888/v1/chat/completions`).
  - O parser do juiz também é frágil (`juiz_llm.py:71`): lista JSON, `"-80%"` ou texto com dois objetos devolvem `{}`, e tudo cai no fallback lexical.
- **Correção:** `return [], "llm-local"`; tirar `sem_data_local` do fallback (é ruído); JSON mode no juiz.
- **Trade-off:** nenhum relevante.

### 12. ALTO (processo) — Os testes verificam o encanamento, não o comportamento; a etapa 4 não tem nenhum teste integrado
- **Onde:** `tests/test_pipeline.py`, `test_regressao_36.py`, `test_juiz_llm.py`, `test_agente.py`, `test_review.py`, `scripts/ab_descoberta.py`.
- **Evidência:**
  - **Nenhum** teste instancia `Pipeline` com SerpAPI ativo: todos usam `SerpAPIClient(api_key="")`, então a descoberta fica "pulada". Agente → triagem → deep crawl → descoberta_site → juiz → sinais → agregador nunca rodou junto em teste. Os achados 1, 2, 3, 7 e 8 passam despercebidos.
  - `test_regressao_36` só verifica `propensao in (4 valores)` e a presença de etapas, e roda com `usar_llm=False`.
  - Os testes do juiz trocam `resumir` e `termometro` por fakes que devolvem exatamente o esperado. O prompt nunca é exercitado, e não existe caso com `score 0 + relevante true` nem com 3 fontes refutando.
  - `test_agente.py:41-54` codifica como **desejada** a parada bugada do achado 5 ("Bets: o que muda" no aosfatos conta como checagem e dispensa a onda 2).
  - `test_review.py:24-39` testa `implicacao.implicacao()`, que o pipeline **não usa** (só `_fallback_lexico`, `pipeline.py:157`).
  - O A/B (`ab_descoberta.py:52-53,113`) mede `n_checagem` com o mesmo `eh_secao_checagem` usado pelo crítico (métrica circular) e overlap de título, **sem gold** (qual checagem deveria aparecer). Os modos compartilham cache, o que distorce `chamadas`.
- **Correção:**
  - Um golden set pequeno (20–30 afirmações PT-BR com checagem conhecida e URL esperada).
  - Teste integrado com SerpAPI falso + juiz falso *com direções*: 3 refutam → espera `alta`/`media`; 3 neutras → espera `indeterminada`; LLM fora → espera `indeterminada`.
  - A/B medindo recall@k da URL esperada.
  - Um teste de contrato do prompt: o parser aceita a saída de exemplo *e* o exemplo não fixa `relevante`.
- **Trade-off:** curar o golden set dá trabalho, mas sem ele nenhuma mudança de prompt ou agente pode ser avaliada.

### Menores / drift de documentação
- O README descreve "implicação laya" e "Google News 2 queries". O código usa LLM-juiz e orgânico/agente.
- O PROGRESSO.md não menciona o loop 13 (agente).
- `pipeline.py:_overlap` usa `split()` sem tirar pontuação: `"dengue."` ≠ `"dengue?"`, então o overlap de "O ibuprofeno cura a dengue." com "Ibuprofeno cura dengue? É falso" fica em 0.67. O último token das afirmações do LLM, que terminam em ".", nunca casa.
- `n_ref` tem confiança fixa 0.8, sem os tiers corpo/manchete que o lado "sustenta" tem (`pipeline.py:487-513`).

---

## Diagnóstico geral do design de agentes

1. **O "agente" é um plano estático com um crítico quase morto.**
   - A onda 1 é fixa: 2 buscas mais Scholar/News por regex.
   - O crítico só reage a proxies de URL. Com 3 afirmações, o teto impede que ele rode (S3); quando roda, reformula na direção errada (S2).
   - Na prática, é o modo `avancada` executado sequencialmente e mais devagar. **Não vale a pena manter um "agente" que não observa a qualidade da evidência.** Um laço de agente só se justifica se o estado observado incluir o julgamento (houve evidência relevante?), e hoje o juiz roda depois do laço.
2. **O que decide o veredito não é a busca; é a agregação, e ela está quebrada.** Mesmo com busca perfeita, 3 refutações viram BAIXA (achado 1). Neutras marcadas relevantes viram BAIXA (achado 3). LLM fora vira BAIXA (achado 2). A direção de toda evidência precisa sair de um único lugar.
3. **O fluxo entre etapas perde e duplica informação.** Afirmação → query com a frase crua. Resultado → só o snippet (título descartado). Resumidor → juiz recebe só o resumo, sem metadados. Mesma peça julgada 2x (índice + web). Sinais redundantes sobre a mesma peça.
4. **Os fallbacks "honestos" não são honestos no veredito.** O recibo registra limitações, mas o sistema emite uma propensão direcional baseada em relevância fabricada. Com `LLM_DAILY_CAP=50`, o sistema passa a maior parte do tempo nesse modo.
5. **Duas premissas não se sustentam nos dados.** (a) O índice de vereditos tem 1 veredito e é feito de homepages. (b) "Catalogada = curada" deixou de valer: o catálogo cresce sozinho e vazio vira G1.

## Proposta de arquitetura corrigida (curta)

```
entrada
 └─ extrair (LLM, JSON): [{afirmacao autocontida, consulta keywords}]  (≤3)
 └─ por afirmação, laço com orçamento B=3 buscas:
      1. ClaimReview/FactCheck API(consulta)  → evidências com rating estruturado
      2. SerpAPI orgânico(consulta) + site: só hosts dedicados (≤8)
      3. dedupe canônico GLOBAL (índice+web), guarda set de afirmações por URL
      4. top-k por afirmação (overlap normalizado) → deep crawl (catálogo curado)
      5. JUIZ (1 call/lote, itens estruturados: título, veículo, tipo, data, selo, trecho)
           → {stance ∈ SUSTENTA|REFUTA|MENCIONA|FORA, score, citação literal verificada}
      6. crítico = regra sobre o JUIZ: se 0 evidência SUSTENTA/REFUTA e B>0 →
           reformula (LLM: consulta nova a partir da afirmação + títulos vistos; ou
           related_search mais similar) → volta a 2
 └─ tabela única evidencias[] (1 linha por peça independente)
 └─ agregador por direção: n_ref_indep, n_sus_indep, selos (ponderados), sem "cobertura" neutra;
      juiz indisponível ⇒ evidência não julgada ⇒ indeterminada (nunca lexico-top3)
 └─ recibo com motor real por etapa
```

Avaliação: golden set de 20–30 afirmações com checagem conhecida. Métricas: recall@5 da URL da checagem, acurácia de stance do juiz e matriz de confusão da propensão. Rodar antes e depois de cada mudança de prompt ou orçamento.

> Fora do escopo pedido, mas vale registrar: `bot.log` (e possivelmente outros logs do httpx) contém o token do Telegram e a chave da SerpAPI em URLs de requisição (não reproduzidos aqui). Considere rotacionar.
