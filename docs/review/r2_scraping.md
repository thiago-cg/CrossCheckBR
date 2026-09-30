# R2 — Revisão de scraping, descoberta e índice (CrossCheckBR)

Escopo: `br_news_crawler.py`, `factcheck_mvp/{descoberta_site,aprofundar,catalogo,indice}.py`, `data/*.json`, `output*/`, logs `run_*.log`. Só leitura no repo. As sondagens ficam em `scratchpad/` (`probe_indice.py`, `probe_extr.py`, `e2e_aprof.py`, `bm25_rss.py`, `pipe_probe.py`, `ua_probe.py`, HTML em `scratchpad/html/`). As sondagens ao vivo foram feitas em 28/09/2026 com GET simples.

**Resumo:** a suspeita do usuário se confirma. **O índice de vereditos não tem nenhuma checagem utilizável. O crawler não extraiu corpo de artigo de nenhum dos 36 documentos. O "schema CSS gerado por LLM" não existe no código e não é usado em runtime.** Duas partes funcionam melhor do que parece: o extrator regex do deep crawl (`extrair_corpo`) e o próprio BM25, quando recebe um corpus real. O maior ganho que está sendo perdido é o ClaimReview, junto com o RSS.

---

## Achados por severidade

### 1. [CRÍTICO] O índice de vereditos está vazio: 0 checagens reais com veredito correto
- **Onde:** `factcheck_mvp/data/amostra_checagem.json` e `amostra_geral.json`, carregados em `api.py:53-66`, e `pipeline.py:200-270`.
- **Evidência:**
  - O `idx_ver` tem **22 docs**: 17 de `amostra_checagem` mais 5 do tipo `checagem` que vêm de `amostra_geral`. **Só 1 tem `veredito`**, e está errado. É um `VERDADEIRO` atribuído pelo laya a uma *notícia* do ValorInveste sobre golpes, que não é uma checagem. É o "selo VERDADEIRO sobre golpes" que o PROGRESSO.md já registrou como contaminação.
  - De `amostra_checagem`, **11 de 17 URLs** são homepage, `/about/`, `/sobre/`, `/redes-sociais` ou hub (`/fato-ou-boato/`, `/confere/`, `/estadao-verifica`). **17 de 17 têm `corpo_texto` igual a markdown de navegação**, com 30 a 121 links `](http` cada.
  - As únicas 2 checagens reais do conjunto são da Lupa ("É falso que Flávio Bolsonaro não disputará…" e "Vídeo não mostra 'invasão…'"). As duas têm **corpo 0 e veredito None**, embora o veredito esteja escrito no título.
  - **Sondagem com 11 afirmações** (`probe_indice.py`): nenhuma retornou um veredito relevante.
    - "vacina covid causa infertilidade" traz "Estadão Verifica - Estadão" (home, score 12.9), "Boatos.org" (home) e "Agência Tatu" (home).
    - "urnas fraudadas" traz o "Fato ou Fake que Virginia Fonseca…".
    - "ibuprofeno/dengue" e "café cura câncer" não retornam nada: todos os tokens estão fora do vocabulário.
    - O único acerto temático ("vídeo homens armados Amazônia" → Lupa, 35.5) não tem corpo nem veredito.
  - **Pipeline offline** (`pipe_probe.py`, duas afirmações): **16 de 16 fontes retornadas são irrelevantes**. Várias vêm com `confianca=0.85` e `corpo_lido=True`, com quote tirado de menu, por exemplo `'[ FIM DOS JOGOS DE PARIS Acompanhe…'`. No modo live o juiz LLM as descarta, então a saída vira "indeterminada", mas o índice contribui com **zero**.
- **Mecanismo:** o índice é só a amostra de validação do crawler dos itens 2 e 3 abaixo. Ninguém ingere checagens de verdade.
- **Correção:** ingerir as agências por RSS/sitemap e extrair o veredito de ClaimReview ou do título (achados 4 e 5). Para ter ordem de grandeza: os 5 feeds testados trazem **210 checagens hoje**.
- **Trade-off:** exige um job de ingestão periódico. Em troca, remove a dependência de Crawl4AI e do LLM local nesse caminho.

### 2. [CRÍTICO] O crawler não extraiu corpo útil em 36 de 36 documentos, e as falhas do LLM foram registradas como "OK"
- **Onde:** `br_news_crawler.py:1268-1304` (`crawl_articles`), `1107-1140` (`pick_article_urls`), `1036-1058`, e `run_full2.log`.
- **Evidência:**
  - `crawl_report.json` marca `success=True` para 21 de 21 homepages, mas `markdown_len=0` em **todas**. O `fit_markdown` fica vazio porque não há content filter configurado.
  - `output/` (execução "com LLM", modelo `LFM2.5-VL-3B-GGUF`):
    - 15 de 19 com corpo abaixo de 200 caracteres.
    - 9 de 19 URLs são home, seção ou `assine`.
    - veredito preenchido em 0 de 19; data em 1 de 19.
  - O `run_full2.log` tem **18× `No model loaded. Call POST /inference/load first`** a partir das 14:50 (linhas 258 a 486). Em cada um desses casos o script loga `[artigo] OK`. O crawl4ai devolve `[{"error": true, ...}]`, isso vira `raw` não vazio e resulta em artigo com corpo None. A falha fica silenciosa.
  - Quando o LLM respondeu, há **alucinação**:
    - "Famosa **fotógrafa** Virginia Fonseca e o **ator** Vini Jr.", na URL de hub `/fato-ou-fake/` e com data 2024.
    - "Aqui está o conteúdo da página de notícias…" para o `acervo.folha`.
  - `output_checagem/` (sem LLM, laya): **17 de 17 corpos são `raw_markdown` de navegação** e 11 de 17 URLs são home ou institucionais. O laya aceitou veredito em 1 de 17, e esse único está errado (achado 1).
  - A seleção de URL escolheu `/about/`, `/about/partners/`, `/redes-sociais`, `/assine/?componente=menu-swipe`, `/sobre/` e `/mais`. O `pick_article_urls` cai para "primeiros links internos longos" quando o padrão não casa (linhas 1130-1139). O `run_full.log` mostra ainda o bug antigo de mapeamento portal↔URL (g1 → `agencialupa.org/academia`).
- **Mecanismo:**
  - (a) A descoberta de URLs parte da homepage por heurística, apesar de existirem RSS e sitemap.
  - (b) `input_format="fit_markdown"` sem `markdown_generator`/`PruningContentFilter` entrega entrada vazia ou markdown cru. É plausível que o crawl4ai caia para `raw_markdown`, que é basicamente menu.
  - (c) Um modelo VL de 3B com taxa de falha de 100% depois das 14:50 não tem nenhum *health-check* nem contagem de erro.
- **Correção:**
  - Trocar a descoberta por feeds/sitemaps.
  - Trocar o LLM de extração por extrator determinístico mais JSON-LD (achado 4).
  - Se o crawl4ai continuar, usar `DefaultMarkdownGenerator(content_filter=PruningContentFilter())` e fazer o bloco `error:true` contar como falha.
  - Adicionar um gate de qualidade: pelo menos 500 caracteres, densidade de links abaixo de 0.2, URL que não seja hub.
- **Trade-off:** perde-se o LLM "genérico", que aqui só atrapalhou. Browser headless fica reservado para os domínios que dão 403.

### 3. [CRÍTICO para a premissa] Não existe "schema CSS gerado por LLM"; os seletores são escritos à mão, nunca são executados e metade está quebrada
- **Onde:** `br_news_crawler.py:150-583` (`PORTAIS[*].css_hints`, comentados como *"seletores CSS prováveis (fallback rápido / documentação)"*), `1074-1089` (`build_css_article_schema`) e `aprofundar.py:72-100`.
- **Evidência:**
  - `generate_schema` não é chamado em lugar nenhum; aparece só como item futuro no ROADMAP. `JsonCssExtractionStrategy` é importado e **nunca executado**: `crawl_articles` usa `LLMExtractionStrategy` ou markdown.
  - Um grep por `css_selectors`, `article_url_patterns` e `rss` no runtime (`factcheck_mvp/`) não encontra **nenhum consumidor** fora de `descoberta_site` (que só os escreve) e dos testes. O deep crawl usa regex `<p>` e ignora o catálogo.
  - **Validação ao vivo dos seletores** (bs4, `probe_extr.py`, 13 páginas reais):

    | portal | seletor `corpo` do catálogo | caracteres |
    |---|---|---|
    | aos-fatos | `article, .post-content` | **0** |
    | estadao / estadao-verifica | `.n--noticia__content, div[itemprop='articleBody']` | **0** |
    | bbc-brasil | `div[data-component='text-block'], main article` | **0** |
    | g1 / fato-ou-fake | `.mc-article-body…` | 3.6k–7.3k ✔ |
    | boatos | `.entry-content, article` | 4.1k ✔ |
    | e-farsas | `.entry-content, article` | 6.4k, com sidebar misturada ("Médico-robô…") |
    | comprova | `article, .post-content, main` | 4.6k–8.7k, com cabeçalho e rodapé |
    | lupa | — (domínio fora do catálogo) | — |

    Resultado: 3 de 7 portais testados quebrados, 2 com ruído.
  - Os seletores dos 15 portais auto-descobertos são **genéricos por construção** (`article`, `main`). `extrair_schema` inclui `article` se a *substring* `<article` aparece em qualquer lugar do HTML, cards de "relacionadas" inclusive (`descoberta_site.py:176-184`). Nada valida que o seletor extrai o corpo.
- **Mecanismo:** o catálogo é metadado decorativo. Mesmo que os seletores estivessem certos, nada os usa.
- **Correção:** abandonar o seletor por portal como caminho principal (achado 9). Se algum for mantido, que seja *override* opcional, com teste de fumaça por domínio: extrai pelo menos 500 caracteres de 3 URLs recentes do RSS, rodando diariamente.

### 4. [ALTO — maior ganho perdido] ClaimReview e JSON-LD são ignorados; boa parte das agências publica o veredito estruturado
- **Onde:** um grep por `ClaimReview|ld+json|jsonld|NewsArticle|articleBody` no código Python não acha nada fora de strings de seletor. `aprofundar.extrair_corpo` descarta os `<script>`, o JSON-LD junto.
- **Evidência ao vivo** (`probe_extr.py`):

  | agência | ClaimReview? | exemplo de `reviewRating` |
  |---|---|---|
  | Aos Fatos (2/2) | ✔ | `alternateName="falso"`, `ratingValue=1`, `bestRating=6`, `claimReviewed="Lula disse que pobres devem 'morrer cortando cana'"` |
  | Estadão Verifica (2/2) | ✔ (+`Claim`) | `"Enganoso"`, 2/4, `itemReviewed.author="Boatos em redes sociais"` |
  | Comprova (2/2) | ✔ (+`Claim`) | `"Falso"`, `"Enganoso"`, com `datePublished` |
  | Lupa (2/2) | ✘, mas `NewsArticle.articleBody` com 2.9k–3.0k caracteres | veredito no título ("É falso que…", "Não há registros…") |
  | G1 Fato ou Fake | ✘ | veredito no título: **99 de 100** títulos do RSS têm `#FAKE`, `#FATO` ou equivalente |
  | Boatos.org | ✘ | 21 de 25 títulos do RSS no padrão "É falso que…"; o corpo começa com "Boato –" |
  | E-farsas | ✘ | 1 de 15 títulos com marcador; veredito no corpo |
  | UOL Confere, TSE | não testado (403) | — |

  Com ClaimReview o veredito sai com **custo zero, sem LLM e sem heurística**: `claimReviewed` é exatamente o texto que deveria ir para o índice como campo de busca. O schema `ArtigoNoticia` já tem `veredito` e `selo_original` e ninguém os preenche a partir disso.
- **Correção:** `extruct` (ou `json.loads` nos `<script type="application/ld+json">`, como fiz em 20 linhas) roda antes de qualquer extração de corpo:
  - `ClaimReview` → `{claim, rating.alternateName → enum, autor, data, url}`.
  - `NewsArticle.articleBody` → corpo.
  - Sem estruturado: regex de título por agência (`É #FAKE|É #FATO|É falso que|Não é verdade|não mostra`) e, em último caso, o LLM classifica o selo sobre o 1º parágrafo mais o título.
  - Complemento: a **Google Fact Check Tools API** (`claims:search`, `languageCode=pt`) agrega o ClaimReview de todos os publishers. Deve cobrir Aos Fatos, Estadão e Comprova; a cobertura de AFP Checamos e Reuters em PT não foi confirmada. Exige chave de API.
- **Trade-off:** o ClaimReview só existe em cerca de metade das agências, então os padrões de título continuam necessários (baratos e com precisão alta). As escalas de `ratingValue` variam (1–6, 1–4, ausente): normalizar por `alternateName`.

### 5. [ALTO] RSS e sitemap estão no catálogo e são ignorados; eles resolvem descoberta e corpo
- **Onde:** os campos `rss`/`sitemap` do `catalogo.json` não têm leitor. O crawler parte das homepages.
- **Evidência ao vivo:**

  | feed | itens | corpo médio no feed |
  |---|---|---|
  | Aos Fatos | 60 | título e link apenas |
  | Lupa (`agencialupa.org/feed/`) | 10 | 3.8k (`content:encoded`) |
  | Boatos | 25 | 4.7k |
  | E-farsas | 15 | 2.9k |
  | G1 Fato ou Fake | 100 | 7.1k |

  Total: **210 checagens**, contra 17 documentos-lixo hoje. O feed do Comprova está velho (2019) e o do Estadão Verifica deu 404 na URL testada: esses dois precisam de sitemap ou hub.
- **Correção:** um ingestor `feedparser` por agência (cron de 1h), mais sitemap/`news-sitemap` para backfill histórico, mais fetch da página para JSON-LD.
- **Trade-off:** alguns feeds truncam o corpo (Aos Fatos), então o fetch da página continua necessário. Mesmo assim, é um GET por checagem nova, não um crawl de BFS.

### 6. [ALTO] Lupa, a principal agência, nunca tem o corpo lido: domínio errado no catálogo e roteamento por substring
- **Onde:** `catalogo.json` (lupa → `https://lupa.uol.com.br/`), `catalogo.py:70-85` (`por_nome_fonte` casa por substring), `aprofundar.py:104-105` e `pipeline.py:379,396`.
- **Evidência** (`e2e_aprof.py`, ao vivo): as matérias estão em `www.agencialupa.org`.
  - `rotear_fonte({"nome":"Agência Lupa"})` → `catalogada=True` (casa `"lupa"` por substring).
  - Mas `_baixar` → **`erro="fora do catalogo"`**, porque `por_dominio(agencialupa.org)` é None.
  - Como está marcada `catalogada`, também **não passa pela descoberta**. Resultado: Lupa fica sempre só com a manchete.
  - O mesmo *over-match* mapeia "Estadão Mato Grosso" → `estadao` e depois falha no deep crawl.
- **Correção:** `aliases_dominio` por portal (`lupa.uol.com.br`, `agencialupa.org`). Decidir `catalogada` **por domínio da URL**, nunca pelo nome da fonte. `por_nome_fonte` fica só com igualdade exata.
- **Trade-off:** manutenção manual de aliases, mas é pequena (cerca de 20 portais).

### 7. [MÉDIO] O extrator de runtime (`extrair_corpo`) funciona; os tetos de bytes e os bloqueios estragam casos grandes
- **Onde:** `aprofundar.py:72-140`, `descoberta_site.py:87-120`, e `config.py:77,82` (`DEEP_CRAWL_MAX_BYTES=500000`, `DISCOVERY_MAX_BYTES=200000`).
- **Evidência** (HTML real, `probe_extr.py`):
  - Com HTML completo, **13 de 13 páginas** deram 2.4k a 4.0k caracteres de texto real do artigo. A regex de `<p>` com filtro de densidade de links é razoável para WordPress e Globo.
  - **Mas:**
    - (a) **BBC**: HTML de 9 MB, dos quais 500 KB rendem **355 caracteres** (título mais 1 frase). Passa o gate `>=200` e vira `corpo_lido=True` com praticamente nada.
    - (b) **Modo descoberta com 200 KB**: páginas G1 e Estadão (0.6–1.2 MB) dão **65 a 98 caracteres**, e a URL cai em "corpo curto/JS/paywall". Confirmado ao vivo em `descobrir()` para Estadão Verifica e O Globo.
    - (c) **UOL e UOL Confere dão 403** (Akamai) para o UA `factcheck-mvp/0.1`; o TSE também. Confirmado ao vivo.
    - (d) Corte em 12 parágrafos e `trecho_corpo[:2000]`: a seção "Conclusão"/"Classificação" no fim pode ser perdida (plausível; não medido).
    - (e) Entidades HTML não são decodificadas (`&#8226;`, `&#8211;`, `&mdash;` aparecem no trecho) e sobra algum boilerplate ("Adicione o Aos Fatos às suas fontes do Google…").
- **Correção:**
  - Teto de 3–5 MB, ou parar de ler após `</article>`.
  - Checar o JSON-LD `articleBody` primeiro.
  - `trafilatura.extract(html, favor_precision=True)` como extrator principal, mantendo a regex atual como fallback. Não medi o trafilatura porque ele não está instalado, e não instalei nada; a qualidade dele em PT é **plausível** pela literatura.
  - `html.unescape`.
  - UA de navegador e Playwright só para domínios em lista de 403.
- **Trade-off:** mais memória por página. O trafilatura adiciona dependência (lxml) e cerca de 20–50 ms por página.

### 8. [MÉDIO] BM25 funciona bem sobre corpus real; o limiar e a confiança atuais não servem para nada
- **Onde:** `indice.py:52,65-71`, `config.py:59` (`INDICE_SCORE_MIN=1.0`) e `pipeline.py:210` (`conf=0.4+0.15*score`).
- **Evidência:**
  - O **BM25Plus soma `delta·idf` a todo documento** para qualquer termo do vocabulário. Para "urnas fraudadas", **todos os 22 docs** marcam ≥1.34, acima do piso de 1.0; o filtro `scores<=0` nunca dispara. A confiança satura em 0.9 com score ≥3.3.
  - No corpus RSS real (210 docs, `bm25_rss.py`), consultas fora do tema marcam **8 a 22**: "ibuprofeno piora a dengue" traz "Taylor Swift…" com 8.5, e "vacina covid deixa mulheres estéreis" traz "terremoto na Venezuela" com 22.0. Um limiar absoluto é inútil.
  - O `test_busca_sem_match_retorna_vazio` só passa porque todos os tokens da consulta estão fora do vocabulário.
  - **Paráfrases no corpus real:** **7 de 7 no rank 1**. Exemplos: "presidente torrou 7 bi viajando com a primeira-dama" → Lupa "Lula gastou R$ 7,35 bilhões em viagens com Janja"; "CR7 comprou o time do Santa Cruz" → Boatos; "votar serve de prova de vida pro INSS" → G1 #FATO. Nomes próprios carregam o BM25. **O gargalo é o corpus, não o BM25.**
  - Sem stemming, "fraudadas", "fraude" e "fraudes" são tokens distintos (df 0, 2 e 1). Claims sem entidade ("estéreis" vs "infertilidade") ficam fora do alcance lexical.
- **Correção:**
  - Limiar **relativo**: score normalizado pelo auto-score da consulta, ou razão com o top-1.
  - Exigir pelo menos 2 termos de conteúdo distintos casados, incluindo 1 entidade.
  - Stemmer RSLP (NLTK) mais stopwords PT completas.
  - Indexar `claimReviewed` com boost.
  - Híbrido com embedding multilíngue (ex.: `multilingual-e5-small`) para paráfrases sem entidade.
  - Criar um conjunto de avaliação de cerca de 50 pares afirmação→checagem (paráfrases de `claimReviewed`) e medir recall@5.
- **Trade-off:** embeddings trazem dependência e RAM (cerca de 100–500 MB), mas não mudam a interface de `Indice.buscar`.

### 9. [MÉDIO] A descoberta insere automaticamente no `catalogo.json`, poluindo o catálogo, e contradiz a própria docstring
- **Onde:** `pipeline.py:411` (`inserir_no_catalogo` na primeira aparição) contra `descoberta_site.py:7` (*"Nunca há auto-merge em `catalogo.json`"*). Ver o diff não commitado de `catalogo.json`.
- **Evidência:**
  - **15 de 35 portais** vêm de `descoberta-automatica`, 6 deles no diff local de 28/09.
  - Nomes errados, porque sem `og:site_name` o nome vira o `<title>` do artigo: "Ações importantes que ajudam a combater a dengue" (blog de hospital, `hnscpm.org.br`) e "Aliado de Lula, senador de MT quer proibir bets no Brasil | FOLHAMAX…".
  - Todos com seletor `article`/`main` e `tipo=geral`.
  - Efeito na extração: nulo, porque os seletores não são usados. Efeito no ranking: essas fontes passam a `catalogada=True` e ganham peso cheio.
- **Correção:** voltar para fila de propostas pura (já existe `salvar_proposta` e `promover`). Nome por `og:site_name` ou JSON-LD `publisher.name`, senão o domínio.
- **Trade-off:** mais curadoria manual, que é o desenho declarado.

### 10. [BAIXO] Outros detalhes observados
- `extrair_schema` detecta seletor de título "h1" por substring. `tipo` usa regex de hints mais Jev. `detectar_sitemap` só testa `/sitemap.xml` (ignora `robots.txt` `Sitemap:` e `news-sitemap`).
- `_eh_generica` deixa passar `/about/partners/`, `/estadao-verifica/recebeu-algum-boato…`, `gov.br/saude/pt-br` e o acervo da Folha como "corpo lido" (9 docs não genéricos com corpo, todos lixo).
- Há duplicatas no índice (e-farsas `http` e `https`; TSE com e sem barra final): falta normalizar a URL canônica (`<link rel=canonical>`).

---

## Premissa 4 — "gerar schema CSS por portal com LLM uma vez e reusar": é robusta?

**Não, e na prática ela nem foi implementada.** Mesmo implementada, seria frágil neste domínio:

| Critério | CSS por portal (LLM 1x) | trafilatura / readability | JSON-LD (NewsArticle / ClaimReview) | RSS `content:encoded` | ReaderLM-v2 (HTML→MD/JSON) |
|---|---|---|---|---|---|
| Drift de layout | quebra em silêncio (3 de 7 já quebrados) | robusto (heurística genérica) | muito robusto (SEO do Google depende dele) | robusto | robusto |
| Listagem vs artigo | não distingue; o crawler pegou hubs | sai pouco texto em hubs (sinal útil) | `@type` distingue (NewsArticle/ClaimReview vs WebPage) | só artigos | não distingue |
| AMP / JS | precisa de seletor por variante; JS exige browser | HTML estático basta nas agências (13/13) | presente no HTML estático | n/a | precisa do HTML renderizado |
| Veredito | precisa de seletor de "selo" mais LLM | não extrai | **`reviewRating.alternateName`** | no título (G1/Boatos/Lupa) | extrai se pedir, mas pode alucinar |
| Custo | 1 LLM por portal mais manutenção | cerca de 20–50 ms, CPU | cerca de 1 ms | 1 GET por feed | 1.5B parâmetros; lento em CPU com HTML de 0.5–9 MB; licença CC-BY-NC (plausível, verificar) |
| Cobertura fora do catálogo | nenhuma | total | parcial | nenhuma | total |

Conclusão: CSS por portal serve, no máximo, como *override* validado para 1 ou 2 portais problemáticos. A ordem certa é **JSON-LD → trafilatura → regex atual → browser/LLM**.

---

## Veredito sobre a premissa de scraping

**Como está hoje, a camada de scraping e descoberta não entrega nada relevante ao objetivo.** O índice de vereditos, pensado como o "caminho feliz 80%", tem **0 checagens utilizáveis**. O crawler Crawl4AI+LLM produziu **0 de 36 corpos de artigo**, com erros do LLM registrados como sucesso e alucinações. O catálogo de seletores é decorativo: nada o lê, e metade está quebrada. A Lupa nunca é lida por um erro de domínio.

O que se salva:
- (a) o `extrair_corpo` de runtime, que lê bem fact-checkers com HTML estático;
- (b) o BM25, que no corpus real acerta 7 de 7 paráfrases;
- (c) o juiz LLM, que hoje evita que o lixo do índice vire veredito.

A premissa certa não é "descobrir a estrutura de cada site com LLM". É **consumir os canais que as agências já publicam para máquinas**: RSS, sitemap, ClaimReview e NewsArticle em JSON-LD.

## Pipeline de extração proposto

```
[A] INGESTÃO DE CHECAGENS (cron 1h; backfill via sitemap)
  por agência: RSS/news-sitemap (feedparser) → URLs novas (dedupe por canonical)
  → GET (UA de navegador; teto 5 MB; Playwright só p/ domínios 403: UOL, TSE)
  → JSON-LD (extruct):
       ClaimReview → claim=claimReviewed, veredito=norm(reviewRating.alternateName),
                     autor=itemReviewed.author, data=datePublished       [Aos Fatos, Estadão, Comprova]
       NewsArticle.articleBody → corpo                                   [Lupa]
  → sem ClaimReview: regex de título por agência (É #FAKE/#FATO, É falso que, Não é verdade…)
                     → fallback LLM só p/ mapear o selo (título + 1º parágrafo, taxonomia fechada)
  → corpo: articleBody ▸ trafilatura(favor_precision) ▸ extrair_corpo atual; html.unescape
  → gate: corpo ≥500 chars, link-density <0.2, URL não-hub, veredito ∈ enum ou None explícito
  → (opcional) Google Fact Check Tools API claims:search pt como 2ª fonte de ClaimReview

[B] ÍNDICE
  campos: claim (boost 3x) + título (2x) + corpo; RSLP + stopwords PT; BM25 Okapi
  + denso multilíngue (e5-small) → fusão RRF; corte relativo (≥ X% do top-1) + ≥2 termos de conteúdo
  eval: 50 pares afirmação→checagem, reportar recall@5 no CI

[C] DEEP CRAWL / DESCOBERTA (SerpAPI em runtime)
  catalogada = por domínio (com aliases), nunca por nome; mesmo extrator de [A]
  (JSON-LD → trafilatura → regex), teto 5 MB, UA de navegador
  site novo → só proposta (sem auto-merge); nome via og:site_name/publisher.name
  ClaimReview achado numa URL da SerpAPI → vira selo direto (mesmo fora do catálogo)

[D] br_news_crawler.py → aposentar como fonte do índice (fica como ferramenta de exploração);
    se mantido: content filter no markdown, erro de LLM = falha, health-check do modelo.
```

Ordem de ganho por esforço:
1. Ingestor RSS com ClaimReview e regex de título (resolve os achados 1, 4 e 5; cerca de 200 linhas).
2. Aliases de domínio da Lupa e `catalogada` por domínio (achado 6).
3. Teto de bytes e `articleBody`/trafilatura no `extrair_corpo` (achado 7).
4. Limiar relativo e stemming no BM25, mais o conjunto de avaliação (achado 8).
5. Desligar o auto-merge (achado 9).
