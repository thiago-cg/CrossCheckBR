# CrossCheckBR — Checagem cruzada de fatos em larga escala

Chatbot (Telegram, com API preparada para web futura) que recebe **texto, título ou link** de notícia e devolve **propensão a desinformação em escala (baixa/média/alta)** — nunca um veredito binário — com evidências linkadas, % do modelo e o passo a passo auditável.

## Como funciona (pipeline em 7 etapas)

```
[1 recebe] → [2 afirmações] → [3 índice de vereditos] → [4 descoberta fresca]
→ [5 implicação laya] → [6 corroboração] → [7 sinais] → [agregador] → resposta + recibo
```

1. **Recebimento** — só texto/título/link (fotos e vídeos recebem orientação, não análise).
   Link de portal monitorado é lido direto (allow-list anti-SSRF); fora do catálogo, pede-se o texto.
2. **Afirmações** — o texto é quebrado em afirmações factuais atômicas (LLM local; laya não gera texto, então aqui o LLM é inevitável — com fallback determinístico).
3. **Índice de vereditos** — cada afirmação é buscada (BM25) no índice das **13 agências de checagem**. É o caminho feliz e barato: se Lupa/Aos Fatos/Comprova já checaram, 80% resolvido em segundos.
4. **Descoberta fresca (SerpAPI Google News)** — 2 queries por afirmação (neutra + dirigida a checagem, `hl=pt-br&gl=br`), com cache de 1h. Sem chave, degrada com elegância para só-índice.
5. **Implicação (laya)** — para cada candidato: *"o texto confirma a afirmação?" / "refuta?"* (`noul`, ~ms, calibrado). Só o que passa no piso de relevância vira evidência. **Sem denylist**: nenhum domínio é bloqueado; o filtro é relevância por consulta.
6. **Corroboração** — conta veículos **independentes** (republicação conta 1x) e difere campos (datas, vereditos) entre fontes.
7. **Sinais + agregação** — modelo próprio de fake news (com % explícita) + LLM de padrões de desinformação → agregador ponderado em escala, redação 100% por templates neutros (LLM nunca redige a conclusão, para não enviesar).

## Estratégias de checagem (por que cada peça existe)

| Estratégia | Papel | Por que |
|---|---|---|
| **Índice próprio de vereditos** | Checagem histórica com selo | Google News quase nunca retorna fact-checks; selo + taxonomia + curadoria da equipe são insubstituíveis |
| **SerpAPI (descoberta)** | Frescor + amplitude | Cobre o que aconteceu hoje; o índice cobre o que já foi checado |
| **laya primeiro** | tipo/editoria/veredito + confirma/refuta | Decisão calibrada em ms, sem alucinação; LLM só onde há geração |
| **Corroboração multi-veículo** | Convergência/divergência | Mesma info em N independentes pesa; ausência cobra |
| **Agregador por pesos** | Propensão, nunca binário | Veredito + corroboração + modelo + padrões, cada um com confiança |
| **Recibo por etapa** | Transparência total | Cada resposta lista etapas, fontes, limitações e descartes |

Regra-mestra: **implicação domina selo** — selo VERDADEIRO numa peça que refuta a afirmação conta contra a afirmação. E evidência só-título vale menos que corpo lido (deep crawl é o próximo passo).

## Como ele busca tudo (catálogo de 20 portais)

`br_news_crawler.py` (Crawl4AI + LLM local Unsloth + classificador laya opcional) percorre **7 veículos gerais** (G1, CNN, UOL, Folha, Estadão, BBC Brasil, Poder360) e **13 de checagem** (Fato ou Fake, Aos Fatos + Radar, Lupa, Comprova, Fato ou Boato/TSE, UOL Confere, Estadão Verifica, E-Farsas, Boatos.org, Agência Tatu, ValorInveste anti-golpes, Saúde sem Fake News, Fiocruz/Canal Saúde), gerando `portais_schema.json`: homepage, RSS/sitemap, padrões de URL, seletores CSS, estratégia e responsável por equipe. Quando a busca encontra domínio novo, o catálogo prevê expansão **uma vez por domínio** (gerar schema → validar → fila de aprovação).

## Como ele responde (exemplo)

> **Propensão: MÉDIA** — Aumentam: selo do portal (Lupa): FALSO + fonte refuta. Reduzem: 2 manchetes sustentam (só título). *Isso não é um veredito…*
> Modelo de detecção: 0.81 (PLACEHOLDER). Fontes consultadas (com links)… Passo a passo: recebimento:ok; base-checagem:ok; … Limitações: … Para avaliar você mesmo: ① compare datas ② quem assina? ③ aparece em +1 veículo independente?

## Como rodar

```bash
pip install -r requirements-mvp.txt
cp .env.mvp.example .env   # TELEGRAM_TOKEN; SERPAPI_KEY opcional

python -m pytest tests/ -q            # 20 testes
uvicorn factcheck_mvp.api:app --port 8000 &
python -m factcheck_mvp.telegram_bot  # @fn_tic_bot
```

API: `GET /saude`, `GET /portais`, `POST /checar {"tipo":"texto|titulo|link","conteudo":"..."}`.
Crawler: `pip install -r requirements.txt && crawl4ai-setup && python br_news_crawler.py --offline` (só contrato) ou `--classifier laya --max-artigos 2` (coleta real; LLM local em `http://127.0.0.1:8888/v1`).

## Estrutura

```
factcheck_mvp/   pipeline.py, schemas.py, catalogo.py, indice.py, afirmacoes.py,
                 serpapi_layer.py, implicacao.py, corroboracao.py, modelo_fake.py,
                 padroes_llm.py, agregador.py, api.py, telegram_bot.py, data/
br_news_crawler.py  crawler Crawl4AI dos 20 portais -> schemas reutilizáveis
tests/  20 testes (contratos, neutralidade, pipeline offline, regressões de review)
```

## Limitações honestas (também saem no recibo de cada resposta)

Modelo de detecção ainda é placeholder; implicação hoje é em manchetes (deep crawl pendente); paywalls/WAFs restringem alguns portais; pesos do agregador são priors até calibração com dados rotulados. Privacidade: o texto é comparado com bases públicas (inclui consulta web externa) — não envie dados pessoais; segredos só via ambiente.
