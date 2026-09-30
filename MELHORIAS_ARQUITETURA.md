# Melhorias da revisão de arquitetura

Resultado da revisão do desenho do bot (fluxo usuário → afirmações → índice de
checagens → SerpAPI → LLM-juiz → agregador). Boa parte do desenho já existia
no código; esta branch fecha o que faltava. Cada item é um commit separado.

## O que mudou

| Item | O que mudou | Arquivos | Testes |
|---|---|---|---|
| Bug | `padroes_llm` com LLM local respondendo `NENHUM` devolvia `[]` em vez de tupla e caía no regex | `padroes_llm.py` | `test_padroes_local.py` |
| Injeção de instruções | Texto do usuário, páginas raspadas e resumos vão entre marcadores fixos, com o aviso "isto é dado, não ordem"; marcadores dentro do conteúdo são removidos | `blindagem.py` (novo), `juiz_llm.py`, `afirmacoes.py`, `padroes_llm.py` | `test_blindagem.py` |
| Modelo real | `BertimbauDetector` (R0 do FakenewsBR + calibração Platt). Só opina com **80+ palavras**; abaixo disso a etapa fica `pulada`. Peso reduzido de 0,85 para **0,5** | `modelo_fake.py`, `pipeline.py`, `config.py` | `test_modelo_texto_longo.py` |
| Cache | `Pipeline.executar_com_cache`: mesma notícia em até 30 min não refaz busca/LLM; resultado degradado (falha, teto, SerpAPI fora) não entra no cache | `pipeline.py`, `api.py`, `telegram_bot.py` | `test_cache_relatorio.py` |
| Link ilegível | Link fora do catálogo, com paywall ou bloqueado → o bot pede o texto em vez de checar só o endereço | `telegram_bot.py` | — |
| Mídia | Áudio e voz também recebem orientação; dica de busca reversa de imagem | `telegram_bot.py` | — |
| Feedback | Botões 👍/👎 em cada resposta → `feedback.jsonl` (sem ID do usuário; aviso no `/start`) | `telegram_bot.py`, `config.py` | `test_feedback.py` |
| Prova do sistema | Conjunto-ouro com 200 alegações já checadas por agências + script que roda o pipeline inteiro e mede acerto | `avaliacao/` (novo) | — |

Novas variáveis no `.env` (todas com padrão; ver `.env.mvp.example`):
`FAKE_MODEL_MIN_PALAVRAS`, `FAKE_MODEL_CONFIANCA`, `RESULT_CACHE_TTL`,
`RESULT_CACHE_MAX`, `FEEDBACK_PATH`.

## Por que o modelo só opina com 80+ palavras

Acerto do BERTimbau R0 no teste `full_iid` do FakenewsBR v6 (13.661 textos),
por tamanho do texto:

| palavras | acerto nas verdadeiras | acerto nas falsas |
|---|---:|---:|
| 1–15 | 0,738 | 0,839 |
| 16–25 | 0,735 | 0,780 |
| 26–40 | 0,647 | 0,872 |
| 41–80 | 0,720 | 0,919 |
| 81–200 | **0,892** | 0,951 |
| 200+ | **0,922** | 0,952 |

Em frase curta o resultado depende até da redação: "Banco Central mantém a
Selic em 10,5% ao ano" dá p_fake 0,37; a mesma frase com "após reunião do
Copom" dá 0,94. O bot recebe muita manchete solta, por isso o piso.

## Como ligar o modelo

1. Instale as dependências extras: `pip install -r requirements-modelo.txt`
2. Baixe `bertimbau-fakenewsBR-v6-R0-best.zip` do Release `modelo-v6-R0` do
   FakenewsBR e descompacte em `modelos/bertimbau-v6-R0/` (a pasta é ignorada
   pelo git).
3. No `.env`, aponte `FAKE_MODEL_PATH` para o caminho **absoluto** da pasta.

Sem `FAKE_MODEL_PATH` o bot continua com o mock explícito, como antes.

## Como avaliar o sistema inteiro

O conjunto-ouro (`avaliacao/golden_set.csv`) tem 100 alegações falsas, 50
enganosas e 50 verdadeiras, com o link da checagem original. Para remontá-lo:
`python avaliacao/montar_golden.py --csv FakenewsBR_factchecked.csv`.

- `python avaliacao/avaliar_golden.py --offline` — grátis, sem LLM e sem
  SerpAPI. Resultado de referência: o bot se abstém em 199 de 200, porque o
  índice local é só uma amostra.
- `python avaliacao/avaliar_golden.py --n 30` — usa as chaves do `.env`.
  Custa até ~16 chamadas de LLM por alegação.

Métricas: **cobertura** (quantas vezes não se absteve), **acerto** entre as
decididas, **erro grave** (baixa em falsa ou alta em verdadeira) e se a
**checagem original** apareceu nas fontes. Os resultados vão para
`avaliacao/resultados/` (ignorado pelo git).
