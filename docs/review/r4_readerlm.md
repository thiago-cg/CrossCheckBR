# R4 — ReaderLM-v2 (Jina AI) via GGUF `mradermacher/ReaderLM-v2-GGUF` no pipeline de checagem PT-BR

Data: 2026-09-28. Experimento de aprendizado, não produção. Nenhum arquivo do repositório foi editado.

## 0. TL;DR

- **O que é:** Qwen2.5-1.5B-Instruct (1,54B parâmetros) fine-tunado pela Jina só para duas coisas: HTML→Markdown e HTML→JSON com schema. Contexto de 512K tokens (entrada e saída somadas), 29 idiomas (português incluso). Licença **CC-BY-NC-4.0**: serve para experimento; para produto comercial precisa de acordo com a Jina.
- **Sua máquina:** Apple **M4** (arm64, GPU de 10 núcleos, 120 GB/s), **24 GB** de RAM. O modelo cabe com folga.
- **Já está baixado e registrado:** o Unsloth Studio que roda em `127.0.0.1:8888` já lista `mradermacher/ReaderLM-v2-GGUF` com quant `Q8_0` (`loaded:false`). O arquivo está em `~/.cache/huggingface/hub/models--mradermacher--ReaderLM-v2-GGUF/.../ReaderLM-v2.Q8_0.gguf` (1,8 GB). Não baixei nada; só consultei `GET /v1/models` e o cache.
- **Veredito:** o ReaderLM-v2 **não substitui** JSON-LD nem trafilatura. Ele vale como **último fallback** antes da falha explícita, e para achar o **veredito/afirmação em texto livre** em páginas sem `ClaimReview`. Para gerar os seletores CSS por portal ele não serve: não é um modelo instrucional geral, então mantenha o LLM generalista para isso.
- **Achado empírico (sondagem de hoje):** Estadão Verifica e Aos Fatos (páginas de checagem) **publicam `ClaimReview` em JSON-LD**, com veredito em `reviewRating.alternateName` ("Falso"/"falso"). Nessas páginas o LLM é desnecessário. O G1 Fato ou Fake (página-resumo testada) **não tem ClaimReview**: é aí que o ReaderLM entra.

---

## 1. O que é o ReaderLM-v2

| Item | Valor | Fonte |
|---|---|---|
| Parâmetros | 1,54B (decoder-only; 28 camadas, hidden 1536, 12 query heads, **2 KV heads**, head 128) | model card |
| Base | Qwen2.5-1.5B-Instruct (`Qwen2ForCausalLM`, `rope_theta=5e6`) | model card, `config.json` |
| Contexto | "up to 512K tokens (combined input and output)"; `max_position_embeddings = 512768` | model card, `config.json` |
| Tarefas | HTML→Markdown (conteúdo principal), HTML→Markdown instruído, **HTML→JSON com JSON Schema** | model card, blog |
| Idiomas | 29 no total; a lista cita explicitamente **Portuguese** | model card |
| Licença | **CC-BY-NC-4.0**. A versão "ReaderLM-v2-pro" é exclusiva para clientes enterprise; uso comercial exige contato com a Jina | model card, blog |
| Paper | arXiv 2503.01151 (Wang et al., mar/2025): pipeline de dados draft→refine→critique com Qwen2.5-32B; SFT + DPO + self-play | arXiv |

**Implicação da licença:** NC = não comercial. Para um experimento de aprendizado ou pesquisa acadêmica, pode usar. Se o projeto virar produto (bot de checagem monetizado, serviço para cliente ou uso dentro de empresa com fim comercial), é preciso licença da Jina ou trocar o componente. Os *outputs* não ficam "contaminados" pela licença de forma clara, mas isso é interpretação jurídica: **não verificado**, consulte um advogado se for comercializar.

### 1.1 Prompt e chat template oficiais (copie exatamente)

O chat template é ChatML e **injeta automaticamente** um system prompt da Jina quando você não manda um. Isso está no `tokenizer_config.json` e **embutido no GGUF** (verificado via API do HF, campo `gguf.chat_template`):

```jinja
{% for message in messages %}{% if loop.first and messages[0]['role'] != 'system' %}{{ '<|im_start|>system
You are an AI assistant developed by Jina AI.<|im_end|>
' }}{% endif %}{{'<|im_start|>' + message['role'] + '
' + message['content'] + '<|im_end|>' + '
' }}{% endfor %}{% if add_generation_prompt %}{{ '<|im_start|>assistant
' }}{% endif %}
```

→ **Mande só uma mensagem `user`, sem `system`.** O servidor aplica o template. `eos_token = <|im_end|>`.

Conteúdo da mensagem `user` (função `create_prompt` do model card):

**HTML → Markdown**
```
Extract the main content from the given HTML and convert it to Markdown format.
```html
{HTML_LIMPO}
```
```

**HTML → JSON com schema** (a instrução fala em "news threads" porque vem do demo do HackerNews; é o texto oficial, mantenha-o igual)
```
Extract the specified information from a list of news threads and present it in a structured JSON format.
```html
{HTML_LIMPO}
```
The JSON schema is as follows:```json
{SCHEMA}
```
```
(Atenção: não há quebra de linha entre `as follows:` e a cerca ```` ```json ````. É assim no original.)

A saída vem normalmente dentro de uma cerca ```` ```json … ``` ````, então o parser precisa removê-la.

### 1.2 Parâmetros de geração recomendados

- Model card: `temperature=0, do_sample=False, repetition_penalty=1.08, max_new_tokens=1024`.
- **Pegadinha:** o `generation_config.json` do repo traz outros valores: `do_sample=true, temperature=0.65, top_k=20, top_p=0.8, repetition_penalty=1.08`. Um servidor que aplique "recomendações por modelo", como o Unsloth Studio diz fazer, pode usar **0.65**. **Mande `temperature: 0` explicitamente em toda requisição.**
- Pré-limpeza oficial (regex, com flags `IGNORECASE|MULTILINE|DOTALL`): remover `<script>`, `<style>`, `<meta>`, comentários `<!-- -->`, `<link>`; opcionalmente esvaziar `<svg>` e trocar imagens base64 por `src="#"`.
- Colab T4 (sem bf16/FA2): cerca de 67 tok/s de entrada e 36 tok/s de saída. Para produção a Jina recomenda RTX 3090/4090.

---

## 2. Quantizações GGUF e como servir

### 2.1 `mradermacher/ReaderLM-v2-GGUF` (quants estáticas, fev/2025)

Tamanhos reais via API do HF:

| Quant | Tamanho | Nota do repo |
|---|---|---|
| Q2_K | 0,75 GB | |
| Q3_K_S / Q3_K_M / Q3_K_L | 0,86 / 0,92 / 0,98 GB | "lower quality" |
| IQ4_XS | 1,03 GB | |
| Q4_K_S / **Q4_K_M** | 1,07 / 1,12 GB | "fast, recommended" |
| Q5_K_S / Q5_K_M | 1,26 / 1,29 GB | |
| Q6_K | 1,46 GB | "very good quality" |
| **Q8_0** | **1,89 GB** | "fast, best quality" (já está no seu cache) |
| f16 | 3,56 GB | "overkill" |

**Versão i1 (imatrix):** `mradermacher/ReaderLM-v2-i1-GGUF` existe, de IQ1_S (0,44 GB) até **Q6_K (1,27 GB)**. **Não tem Q8_0.** Traz `imatrix.dat`.

**Oficial Unsloth:** `unsloth/ReaderLM-v2-GGUF` **não existe** (a API do HF responde 401/inexistente; também não aparece na busca). Outras opções: `mlx-community/jinaai-ReaderLM-v2` (MLX, nativo de Apple Silicon), `DevQuasar-7/...`, `matrixportalx/...`, além de vários repos de quant única.

**Qual usar para extração fiel: Q8_0.** Motivos:
1. Com 1,5B parâmetros, cada peso "carrega" mais, e a regra prática da comunidade é que modelos pequenos perdem mais com quantização agressiva. **Não achei medição publicada** de F1 de JSON por quant para o ReaderLM (**não verificado**).
2. A diferença de RAM é irrelevante na sua máquina (1,9 GB contra 1,1 GB).
3. Em Metal, Q8_0 é rápido. O custo dominante aqui é o **prefill** de milhares de tokens de HTML, que é limitado por computação, não por banda de memória, então quant menor quase não acelera.
4. imatrix só faz diferença relevante de Q4 para baixo. Além disso, a calibração do mradermacher usa texto genérico, não HTML, e o efeito disso é **não verificado**.

Q6_K é a alternativa se quiser economizar. Q4_K_M só para comparar a degradação no seu próprio conjunto de teste (vale como exercício de aprendizado).

### 2.2 Servir com endpoint OpenAI-compatível

**Unsloth Studio (o que o projeto já usa, porta 8888).** O CLI local (`~/.local/bin/unsloth`) tem `unsloth run` (alias de `unsloth studio run`), que aceita `--model`/`-hf` com sintaxe `org/repo:QUANT` (igual ao llama-server), `--max-seq-length`, `--parallel/-np` (padrão **4**, e cada slot recebe **ctx/N** de KV cache), `--temperature`, `--repetition-penalty`, `-p 8888`, `--api-only`, `--enable-tools/--disable-tools`. Por baixo ele usa o `llama-server` do `~/.unsloth/llama.cpp`.

```bash
# Opção A: servidor dedicado ao ReaderLM numa porta separada (não derruba o LFM2.5 da 8888)
unsloth run -hf mradermacher/ReaderLM-v2-GGUF:Q8_0 \
  --max-seq-length 65536 -np 1 \
  --temperature 0 --repetition-penalty 1.08 \
  --disable-tools --api-only -p 8889
# depois: GET http://127.0.0.1:8889/v1/models  -> use o "id" retornado
```
Se o Studio da 8888 trocar de modelo sob demanda quando recebe `model: "mradermacher/ReaderLM-v2-GGUF"`, dá para usar a mesma porta. Esse comportamento (auto-load/swap) é **não verificado**: não testei para não descarregar o modelo que você tem ativo. A doc diz que a API pode exigir `Authorization: Bearer sk-unsloth-…`; o seu `/v1/models` respondeu sem chave. Se `chat/completions` exigir, crie a chave em Settings → API.

Pontos importantes para esse modelo no Unsloth:
- `-np 1`. Com o padrão 4 slots, uma janela de 64k vira 16k por requisição, e o HTML é truncado.
- `--max-seq-length` explícito. O padrão "0 = model default" = 512.768 tokens, cerca de 15 GB só de KV. O modo `auto` corta a janela para caber na memória, mas é melhor fixar o valor.
- `enable_tools: false` na requisição (ou `--disable-tools`). O servidor liga web-search e execução de código por padrão, e isso é inútil e arriscado para extração.

**llama.cpp puro** (o binário já existe em `~/.unsloth/llama.cpp/llama-server`):
```bash
~/.unsloth/llama.cpp/llama-server -hf mradermacher/ReaderLM-v2-GGUF:Q8_0 \
  -c 65536 -np 1 -fa on --temp 0 --repeat-penalty 1.08 --port 8081
# ou apontando para o arquivo já em cache:
~/.unsloth/llama.cpp/llama-server -m ~/.cache/huggingface/hub/models--mradermacher--ReaderLM-v2-GGUF/snapshots/*/ReaderLM-v2.Q8_0.gguf -c 65536 -np 1 --port 8081
# endpoint: http://127.0.0.1:8081/v1/chat/completions (o template vem do GGUF)
```
(A flag `-fa` muda de sintaxe entre versões: pode ser `-fa`, `-fa on` ou `--flash-attn`. Confira com `--help`.)

**Ollama** (0.32.5 instalado):
```bash
ollama pull hf.co/mradermacher/ReaderLM-v2-GGUF:Q8_0
# endpoint OpenAI: http://127.0.0.1:11434/v1
```
Atenção: o Ollama escolhe um template Go "parecido" a partir do `chat_template` do GGUF. Pode perder o system da Jina (efeito **não verificado**). O `num_ctx` padrão também é pequeno e **trunca o HTML em silêncio**. Ajuste com `OLLAMA_CONTEXT_LENGTH=65536 ollama serve` ou com um Modelfile (`PARAMETER num_ctx 65536`).

**LM Studio:** `lms get mradermacher/ReaderLM-v2-GGUF` (ou pela busca da UI), depois `lms server start`, em `http://127.0.0.1:1234/v1`. Não está instalado aqui. **Não verificado.**

**MLX (alternativa nativa Apple):** `pip install mlx-lm && mlx_lm.server --model mlx-community/jinaai-ReaderLM-v2 --port 8082`. Costuma ter prefill mais rápido em Apple Silicon (**não verificado** para este modelo).

**O template está embutido no GGUF?** **Sim.** O metadado `tokenizer.chat_template` do repo mradermacher é idêntico ao do `tokenizer_config.json` original (inclui o system "You are an AI assistant developed by Jina AI."). Servidores baseados em llama.cpp o aplicam automaticamente.

---

## 3. Limitações conhecidas

1. **Alucinação e perda de texto em HTML longo.** Na discussão #13 do HF, um usuário relata saída cortada e outro diz que, quando consegue saída longa, ela vem "accompanied by a lot of hallucinations, and you end up losing the original text". Na #5, o usuário ubergarm diz que o modelo "can still hallucinate inaccurate information".
2. **Loops e repetição.** A v1 tinha degeneração forte; a v2 "greatly alleviates" com contrastive loss (model card), mas não elimina. Na #12 há relato de "endless repetitive content output" no vLLM com certos HTMLs, além de respostas vazias. Mitigação: `repeat_penalty 1.08`, `max_tokens` limitado e tratar `finish_reason == "length"` como falha.
3. **Placeholders inventados no JSON.** No exemplo oficial com schema, campos ausentes voltam como `"Unknown"` em vez de `null`. Na #10, as quebras `\n` somem dentro de strings do JSON. **Valide sempre** e converta "Unknown" em nulo.
4. **Instrução livre é fraca.** Para a própria Jina, "the most reliable results come from HTML-to-Markdown conversion". Tarefas com raciocínio em várias etapas não estão cobertas pelo treino. Classificar o veredito num enum não é tarefa do ReaderLM: extraia o selo literal e normalize depois, com o laya ou com regras.
5. **Lentidão.** Na #12, HTML de 20 a 40 KB levava 4 a 5 minutos numa H20 com transformers (5 a 10 vezes mais rápido com vLLM). Na #5, "a few minutes" numa 3090 Ti.
   - **Estimativa para o seu M4 base (não medida):** o benchmark oficial do llama.cpp (discussion #4167) dá, para Llama-7B no M4 10-GPU, PP512 ≈ 224 tok/s e TG ≈ 13,5 tok/s (Q8_0) / 24 tok/s (Q4_0). Escalando pelo tamanho (1,5B ≈ 1/4,5 dos FLOPs; 1,9 GB contra 7,2 GB), o ReaderLM Q8_0 deve ficar em **~800–1000 tok/s de prefill em contexto curto**, caindo em contexto longo, e **~40–55 tok/s de geração** (Q4_K_M: ~70–80).
   - Na prática: a checagem do Estadão, depois da limpeza, tem ~20k caracteres (**~5–6k tokens**) e deve levar ~10 s de prefill + 1024 tokens de JSON (~20–25 s). A página-resumo do G1 Fato ou Fake tem **1,1 MB bruto, 471 KB com a limpeza oficial e 123 KB com limpeza agressiva (~35k tokens)**, e deve levar **~1–2 min só de prefill**. Um Markdown completo de 3–4k tokens leva mais ~1–1,5 min.
6. **Memória de KV:** 28 camadas × 2 KV heads × 128 × 2 (K,V) × 2 bytes ≈ **28 KB/token** em f16. Isso dá 32k ≈ 0,9 GB, 64k ≈ 1,9 GB, 128k ≈ 3,7 GB e 512k ≈ 15 GB. Na sua máquina, 64k–128k de janela são tranquilos.
7. **Portais BR com muito boilerplate.** A limpeza oficial não basta: o G1 continuou com 471 KB, ~130k tokens, além do sensato. Os pré-cortes (seletor `corpo` do `catalogo.json`, remoção de nav/footer/atributos) são obrigatórios. **Não há benchmark publicado em português** nem em portais BR (**não verificado**; o treino é multilíngue, mas sem números por idioma).
8. **Benchmarks publicados** (blog da Jina, conjunto próprio, sem comparação com trafilatura ou readability):

   | HTML→Markdown (main content) | ROUGE-L | WER↓ | Levenshtein↓ | Jaro-Winkler |
   |---|---|---|---|---|
   | ReaderLM-v2 | **0,84** | 0,62 | **0,22** | **0,82** |
   | GPT-4o-2024-08-06 | 0,69 | **0,41** | 0,40 | 0,75 |
   | Qwen2.5-32B-Instruct | 0,71 | 0,47 | 0,41 | 0,70 |
   | Gemini2-flash-expr | 0,69 | 0,62 | 0,40 | 0,74 |
   | reader-lm-1.5b (v1) | 0,72 | 1,14 | 0,35 | 0,70 |

   | HTML→JSON | F1 | Precision | Recall | Pass-rate |
   |---|---|---|---|---|
   | ReaderLM-v2 | 0,81 | 0,82 | 0,81 | 0,98 |
   | GPT-4o | **0,83** | 0,84 | **0,83** | **1,00** |
   | Qwen2.5-32B | **0,83** | **0,85** | **0,83** | **1,00** |

   No JSON ele fica **abaixo** do GPT-4o e do Qwen-32B, e cerca de 2% das saídas não são JSON válido. No Markdown ele ganha em similaridade de texto, mas perde em WER. Os benchmarks e o dataset são da própria Jina.
   - **trafilatura:** F1 0,945 no ScrapingHub article-extraction-benchmark (newspaper3k 0,912; goose3 0,887), e F1 médio 0,937 com precisão 0,978 na avaliação da própria doc. É determinístico e roda em milissegundos por página. **Não existe comparação direta publicada ReaderLM-v2 × trafilatura** (**não verificado**).

---

## 4. Comparação honesta

| Critério | JSON-LD (`ClaimReview`/`NewsArticle`) | trafilatura | ReaderLM-v2 (Q8_0 local) |
|---|---|---|---|
| Determinismo | total | total | temp 0 ≈ determinístico, mas pode alucinar |
| Velocidade | µs–ms | ~10–50 ms/página | 10 s a 2+ min/página no M4 |
| Fidelidade | é o dado "oficial" do editor | alta no corpo; metadados via JSON-LD, meta tags e heurísticas | boa em Markdown; em JSON F1 ≈ 0,81; inventa "Unknown" |
| Veredito de checagem | **sim** (`reviewRating.alternateName`) | não (extrai só texto) | **sim**, se o selo estiver no HTML |
| Afirmação checada | **sim** (`claimReviewed`) | não | sim (extração), sujeito a alucinação |
| Cobertura BR (sondagem de 28/09/2026) | Estadão Verifica: `ClaimReview`+`Claim`+`NewsArticle`; Aos Fatos: `@type: ["Review","ClaimReview"]` (tipo em **lista**!) em checagens, nada em reportagens; G1 Fato ou Fake (resumo): só `Organization`/`ImageObject` | universal | universal |
| Licença | n/a | Apache-2.0 | CC-BY-NC-4.0 |

Observação: o Google **deixou de exibir rich results de ClaimReview em junho/2025**. O schema continua no schema.org e os checadores BR ainda publicam (verificado hoje no Estadão e no Aos Fatos), mas o incentivo de SEO diminuiu, e a cobertura pode cair com o tempo.

**Onde o ReaderLM agrega valor real:**
- Páginas **sem JSON-LD de checagem** (G1 Fato ou Fake, reportagens do Aos Fatos, sites regionais): extrair o selo literal ("É #FAKE", "Enganoso"), a afirmação e um trecho de evidência do texto livre.
- **Fallback quando o trafilatura falha** ou devolve pouco texto: páginas muito "app-like", listas ao vivo, checagens com vários itens numa página só (o ReaderLM entende a estrutura; o trafilatura achata).
- **Markdown estruturado** de tabelas e listas aninhadas, útil para RAG ou para o LLM-juiz ver a estrutura. O `fit_markdown` do Crawl4AI já cobre parte disso.
- **Aprendizado:** comparar LLM especializado contra heurística contra dado estruturado no mesmo conjunto de URLs.

**Onde é desperdício:**
- Qualquer página com `ClaimReview` válido (Estadão, Aos Fatos, provavelmente Lupa e AFP Checamos, **não verificado** para essas duas).
- Corpo de notícia comum (G1, Folha etc.): trafilatura ou o seletor `corpo` do `catalogo.json` são mais fiéis e 1000× mais rápidos.
- Classificar o veredito no enum do projeto (FATO/FAKE/ENGANOSO…): use regra + laya, que já existem no `br_news_crawler.py`.
- Gerar seletores CSS (tarefa atual do Crawl4AI + LLM): o ReaderLM não segue instruções gerais, e o `LLMExtractionStrategy` do Crawl4AI manda prompts próprios, incompatíveis com o formato em que o modelo foi treinado.

---

## 5. Desenho de integração: cascata

```
HTML bruto (Crawl4AI / httpx)
  │
  ├─1─ JSON-LD: ClaimReview (claimReviewed, reviewRating.alternateName, author, datePublished)
  │        └─ ok (afirmação + veredito) → retorna metodo="jsonld"  (+ completa buracos com trafilatura)
  │
  ├─2─ trafilatura.bare_extraction(with_metadata)  → título/autor/data determinísticos + texto
  │        (sem veredito; o veredito vem do passo 3 ou de regex por portal)
  │
  ├─3─ ReaderLM-v2 (JSON com schema)
  │        pré-corte: seletor catalogo.json → css_selectors.corpo  (+ h1)
  │        pré-limpeza: regex oficial Jina + nav/footer/aside/form + atributos
  │        limite de tamanho → senão falha explícita
  │        temperature 0, repeat_penalty 1.08, max_tokens 1024, finish_reason != length
  │        validação: Pydantic + "Unknown"→None + ANCORAGEM (cada campo precisa existir no HTML)
  │        metadados do trafilatura têm prioridade sobre os do LLM
  │
  └─4─ falha explícita: metodo="falha" + avisos[] (nunca inventar veredito)
        → depois: veredito literal → normalizar p/ enum (laya/regras) → confianca_veredito
```

Código completo, testado **na etapa JSON-LD** contra páginas reais do Estadão e do Aos Fatos (a etapa ReaderLM não foi executada para não carregar o modelo no seu Studio):
`/private/tmp/claude-502/-Users-aluno1-Documents-Default-Project/f5017f27-757f-495a-9f1c-9d22efc3c7b1/scratchpad/cascata_readerlm.py`

Resultado real da etapa 1 (Estadão Verifica): `veredito='Falso'`, `autor='Luciana Marschall'`, `data_publicacao='2026-09-25T09:51:18-03:00'`, `afirmacao_checada='Troca de mensagens entre Daniel Vorcaro…'`, `metodo='jsonld'`.

Trecho essencial (chamada + prompt + validação):

```python
import json, re, os, unicodedata, httpx
from typing import Optional
from pydantic import BaseModel

BASE_URL = os.getenv("READERLM_BASE_URL", "http://127.0.0.1:8888/v1")
MODEL    = os.getenv("READERLM_MODEL", "mradermacher/ReaderLM-v2-GGUF")   # confira em GET /v1/models

class Checagem(BaseModel):
    url: str
    titulo: Optional[str] = None
    data_publicacao: Optional[str] = None
    autor: Optional[str] = None
    afirmacao_checada: Optional[str] = None
    veredito: Optional[str] = None
    trecho_evidencia: Optional[str] = None
    metodo: str = "falha"
    avisos: list[str] = []

SCHEMA = json.dumps({"type": "object", "properties": {
    "titulo": {"type": "string"}, "data_publicacao": {"type": "string"}, "autor": {"type": "string"},
    "afirmacao_checada": {"type": "string", "description": "The claim being fact-checked, verbatim"},
    "veredito": {"type": "string", "description": "Verdict label as written, e.g. Falso, Enganoso, #FAKE"},
    "trecho_evidencia": {"type": "string", "description": "Verbatim sentence that justifies the verdict"}},
    "required": ["titulo", "afirmacao_checada", "veredito"]}, ensure_ascii=False, indent=2)

F = re.I | re.M | re.S
def limpar_html(html: str) -> str:            # regex oficiais da Jina + extras
    for p in (r"<[ ]*script.*?\/[ ]*script[ ]*>", r"<[ ]*style.*?\/[ ]*style[ ]*>", r"<[ ]*meta.*?>",
              r"<[ ]*!--.*?--[ ]*>", r"<[ ]*link.*?>",
              r"<(nav|footer|aside|form|iframe|noscript|button)\b.*?</\1>"):
        html = re.sub(p, "", html, flags=F)
    html = re.sub(r"(<svg[^>]*>)(.*?)(<\/svg>)", r"\1\3", html, flags=re.S)
    html = re.sub(r'<img[^>]+src="data:image/[^;]+;base64,[^"]+"[^>]*>', '<img src="#"/>', html)
    html = re.sub(r'\s(class|style|id|data-[\w-]+|aria-[\w-]+|srcset|sizes)="[^"]*"', "", html)
    return re.sub(r"\s+", " ", html).strip()

def prompt_json(html_limpo: str, schema: str) -> str:     # formato EXATO do model card
    instr = ("Extract the specified information from a list of news threads "
             "and present it in a structured JSON format.")
    return f"{instr}\n```html\n{html_limpo}\n```\nThe JSON schema is as follows:```json\n{schema}\n```"

def chamar(conteudo: str) -> str:
    r = httpx.post(f"{BASE_URL}/chat/completions", timeout=300,
        headers={"Authorization": f"Bearer {os.getenv('UNSLOTH_API_KEY', 'not-needed')}"},
        json={"model": MODEL, "messages": [{"role": "user", "content": conteudo}],  # sem system!
              "temperature": 0, "top_k": 1, "max_tokens": 1024,
              "repeat_penalty": 1.08, "repetition_penalty": 1.08,   # llama.cpp / HF (um é ignorado)
              "enable_tools": False})                                # Unsloth: sem tools server-side
    r.raise_for_status()
    ch = r.json()["choices"][0]
    if ch.get("finish_reason") == "length":
        raise RuntimeError("truncado/loop")
    return ch["message"]["content"]

def _norm(s): return re.sub(r"[^a-z0-9 ]+", " ",
    unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()).split()
def ancorado(v: Optional[str], fonte: str, r=0.8) -> bool:   # anti-alucinação
    if not v: return True
    t, f = _norm(v), set(_norm(fonte))
    return bool(t) and sum(x in f for x in t) / len(t) >= r

def via_readerlm(url: str, html: str) -> Checagem:
    limpo = limpar_html(html)                     # idealmente: recorte antes pelo seletor 'corpo'
    if len(limpo) > 120_000:
        return Checagem(url=url, avisos=["HTML grande demais; recorte com seletor"])
    txt = chamar(prompt_json(limpo, SCHEMA))
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", txt, re.S) or re.search(r"(\{.*\})", txt, re.S)
    d = {k: (None if isinstance(v, str) and v.strip().lower() in {"", "unknown", "n/a", "null"} else v)
         for k, v in json.loads(m.group(1)).items()}
    ck = Checagem(url=url, metodo="readerlm", **{k: d.get(k) for k in Checagem.model_fields
                                                  if k not in ("url", "metodo", "avisos")})
    fonte = re.sub(r"<[^>]+>", " ", limpo)
    for campo in ("afirmacao_checada", "trecho_evidencia", "veredito", "titulo"):
        if not ancorado(getattr(ck, campo), fonte):
            ck.avisos.append(f"{campo} não ancorado → descartado"); setattr(ck, campo, None)
    if not (ck.afirmacao_checada and ck.veredito):
        ck.metodo = "falha"
    return ck
```

(A cascata completa, com `via_jsonld` tratando `@type` em lista e `@graph`, `via_trafilatura` compatível com trafilatura 1.x e 2.x, recorte por `css_selectors.corpo` e a orquestração `extrair_checagem`, está no arquivo `.py` acima.)

### Comandos

```bash
# 0) (já feito na sua máquina) o Q8_0 está em cache; para baixar explicitamente em outra máquina:
#    huggingface-cli download mradermacher/ReaderLM-v2-GGUF ReaderLM-v2.Q8_0.gguf
# 1) servir numa porta dedicada (não mexe no modelo carregado na 8888)
unsloth run -hf mradermacher/ReaderLM-v2-GGUF:Q8_0 --max-seq-length 65536 -np 1 \
  --temperature 0 --repetition-penalty 1.08 --disable-tools --api-only -p 8889
#    alternativa: ~/.unsloth/llama.cpp/llama-server -hf mradermacher/ReaderLM-v2-GGUF:Q8_0 -c 65536 -np 1 --port 8889
curl -s http://127.0.0.1:8889/v1/models
# 2) dependência determinística
pip install trafilatura
# 3) rodar a cascata numa checagem (com seletor do catálogo opcional)
READERLM_BASE_URL=http://127.0.0.1:8889/v1 python cascata_readerlm.py \
  "https://g1.globo.com/fato-ou-fake/..." ".mc-article-body"
```

### Sugestão de experimento (para aprender de fato)
1. Monte cerca de 30 URLs de checagem (10 com ClaimReview, 10 sem, 10 notícias comuns).
2. Use o ClaimReview como **gabarito** para as 10 primeiras e rode o ReaderLM *ignorando* o JSON-LD. Assim você mede a acurácia de veredito e afirmação sem rotular nada à mão.
3. Compare Q8_0, Q6_K e Q4_K_M, com e sem recorte por seletor, registrando tempo, F1 por campo e taxa de "não ancorado".
4. Opcional: teste `response_format: {"type":"json_schema",...}` (gramática do llama-server) para forçar JSON válido. Isso pode brigar com o hábito do modelo de abrir com ```` ```json ```` (**não verificado**; o passthrough pelo Unsloth também é **não verificado**).

---

## Itens marcados como não verificados
- Se o Unsloth Studio carrega o modelo sob demanda pelo campo `model`, se exige API key em `/chat/completions` e se repassa `repeat_penalty`, `repetition_penalty`, `top_k` e `response_format`.
- Degradação de JSON por quantização específica do ReaderLM, e o efeito da calibração imatrix em HTML.
- Qualidade em português ou em portais BR (não há benchmark publicado).
- tok/s no M4: estimativas extrapoladas do benchmark de Llama-7B, sem medição.
- Presença de ClaimReview na Lupa e na AFP Checamos (a sondagem não obteve páginas de artigo delas).
- Comportamento do template no Ollama e no LM Studio.
- Implicação da CC-BY-NC sobre os outputs.

## Fontes
- Model card ReaderLM-v2: https://huggingface.co/jinaai/ReaderLM-v2 (README, `tokenizer_config.json`, `generation_config.json`, `config.json` em /raw/main/)
- Blog Jina (benchmarks, limitações, licença/pro): https://jina.ai/news/readerlm-v2-frontier-small-language-model-for-html-to-markdown-and-json
- Paper: https://arxiv.org/abs/2503.01151
- Discussões HF: https://huggingface.co/jinaai/ReaderLM-v2/discussions/5 · /discussions/10 · /discussions/12 · /discussions/13
- GGUF estático: https://huggingface.co/mradermacher/ReaderLM-v2-GGUF (tamanhos e template via https://huggingface.co/api/models/mradermacher/ReaderLM-v2-GGUF?expand[]=gguf e /tree/main)
- GGUF imatrix: https://huggingface.co/mradermacher/ReaderLM-v2-i1-GGUF
- MLX: https://huggingface.co/mlx-community/jinaai-ReaderLM-v2
- Unsloth API/CLI: https://unsloth.ai/docs/basics/api · https://unsloth.ai/docs/new/studio/chat · https://github.com/unslothai/unsloth (e `unsloth run --help` local)
- llama.cpp server: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md · GGUF+llama.cpp no HF: https://huggingface.co/docs/hub/gguf-llamacpp
- Benchmarks Apple Silicon llama.cpp: https://github.com/ggml-org/llama.cpp/discussions/4167
- Ollama + HF GGUF: https://huggingface.co/docs/hub/ollama
- trafilatura avaliação: https://trafilatura.readthedocs.io/en/latest/evaluation.html · uso Python: https://trafilatura.readthedocs.io/en/latest/usage-python.html · JSON-LD no código: https://github.com/adbar/trafilatura/blob/master/trafilatura/json_metadata.py
- ScrapingHub benchmark: https://github.com/scrapinghub/article-extraction-benchmark
- ClaimReview: https://schema.org/ClaimReview · https://developers.google.com/search/docs/appearance/structured-data/factcheck
- Google remove rich results (jun/2025): https://developers.google.com/search/blog/2025/06/simplifying-search-results · https://www.poynter.org/ifcn/2025/google-claimreview-fact-checks-snippets-removed/
- Sondagem própria (28/09/2026): páginas de estadao.com.br/estadao-verifica, aosfatos.org/noticias, g1.globo.com/fato-ou-fake (scripts `probe.py`/`probe2.py` no scratchpad)
