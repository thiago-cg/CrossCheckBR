# Roadmap — CrossCheckBR

Estado atual: MVP funcional (bot @fn_tic_bot + API + crawler de 20 portais, 20 testes verdes).
Critério de pronto de cada fase: testes verdes + validado ao vivo + recibo exibindo a novidade.

## Fase 1 — Fechar o MVP (funcional completo)
- [ ] **Modelo próprio real** (`train_detector.py`): TF-IDF + LogReg calibrado (`CalibratedClassifierCV`), CLI de treino sobre CSV `texto,rotulo`, eval com CV estratificada; ligar via `FAKE_MODEL_PATH` (interface já pronta). Owner dados: equipe de curadoria.
- [ ] **Deep crawl SerpAPI**: etapa `aprofundar()` top-3 links (allow-list + bloqueio de IP privado + teto de bytes/tempo); implicação passa a usar corpo, não só manchete.
- [ ] **Catálogo vivo**: `catalogo_dinamico.py` — propõe domínio novo 1x, `generate_schema` one-shot no LLM local, fila `propor/aprovar/rejeitar` (CLI), estados `ativo|candidato|rejeitado` com proveniência.
- [ ] **Web mínima**: `GET /` servindo página estática (form + render do `RelatorioChecagem`) na API existente (RF15).
- [ ] Calibrar pesos do agregador nos 36 artigos coletados (sanity, não tuning final).

## Fase 2 — Qualidade e confiança
- [ ] Dataset rotulado PT-BR (amostra de checagens reais com veredito; curadoria por responsável no schema).
- [ ] Harness de eval: precisão do pipeline por RF, ECE das confianças (laya e modelo), taxa de "indeterminada".
- [ ] Ajustar limiares (`LAYA_THRESHOLD`, `RELEVANCIA_MIN`, `INDICE_SCORE_MIN`, tiers de evidência) nos dados.
- [ ] Teste adversarial: manchetes modais/condicionais, sátira, opinião vs. fato.
- [ ] Revisão de neutralidade com leitores leigos (RNF04).

## Fase 3 — Escala e operação
- [ ] Scheduler de crawl (RSS/sitemap por portal, recrawl por frescor) + Postgres + pgvector (troca do BM25 in-memory sem mudar `Indice.buscar`).
- [ ] Workers/filas p/ pipeline (timeout global, concorrência limitada), rate-limit por usuário (Telegram) e por cliente (API), teto de custo SerpAPI/dia.
- [ ] Observabilidade: métricas por etapa (latência, taxa de descarte com motivo), logs sem PII/segredos, alertas.
- [ ] Auth na API + remoção de `responsavel` de respostas públicas (já feito em `/portais`).
- [ ] `requirements.lock` pinado; CI rodando pytest a cada push.

## Fase 4 — Produto
- [ ] Web completa (histórico por usuário, comparação lado a lado de fontes).
- [ ] WhatsApp (novo adapter sobre a mesma API — RNF05).
- [ ] Loop de feedback: usuário marca utilidade → alimenta dataset da Fase 2.
- [ ] Painel da equipe: fila do catálogo, cobertura por responsável, vereditos em divergência.

## Riscos acompanhados
WAF/paywall por portal (ver `observacoes` no catálogo) · custo SerpAPI · dependência de modelo local · LGPD (texto do usuário em serviços externos — aviso já no `/start`).
